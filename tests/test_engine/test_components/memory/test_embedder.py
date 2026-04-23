"""Tests for v12 M7 Task 7.2 — EmbedderComponent + backend factory.

Plan: docs/superpowers/plans/2026-04-21-llm-code-v12-memory-components.md
"""
from __future__ import annotations

import asyncio
import hashlib

import pytest

from llm_code.engine.component import (
    get_input_sockets,
    get_output_sockets,
    is_component,
)
from llm_code.engine.components.memory.embedder import (
    DeterministicHashBackend,
    EmbedderComponent,
    EmbeddingBackend,
    build_embedder_from_config,
    text_sha256_prefix,
)


class _StubConfig:
    """Minimal MemoryConfig duck-type for factory tests."""

    def __init__(self, *, embedder: str = "deterministic", embedder_model: str = "") -> None:
        self.embedder = embedder
        self.embedder_model = embedder_model


# ---------------------------------------------------------------------------
# Backend protocol / DeterministicHashBackend
# ---------------------------------------------------------------------------
class TestDeterministicHashBackend:
    def test_satisfies_protocol(self) -> None:
        backend = DeterministicHashBackend()
        assert isinstance(backend, EmbeddingBackend)

    def test_dimension_default(self) -> None:
        assert DeterministicHashBackend().dimension == 384

    def test_dimension_override(self) -> None:
        assert DeterministicHashBackend(dimension=64).dimension == 64

    def test_invalid_dimension_raises(self) -> None:
        with pytest.raises(ValueError):
            DeterministicHashBackend(dimension=0)
        with pytest.raises(ValueError):
            DeterministicHashBackend(dimension=-5)

    def test_embed_returns_tuple(self) -> None:
        vec = DeterministicHashBackend().embed("hello world")
        assert isinstance(vec, tuple)
        assert all(isinstance(x, float) for x in vec)

    def test_embed_expected_dimension(self) -> None:
        vec = DeterministicHashBackend(dimension=64).embed("hello world")
        assert len(vec) == 64

    def test_embed_deterministic(self) -> None:
        a = DeterministicHashBackend().embed("hello world")
        b = DeterministicHashBackend().embed("hello world")
        assert a == b

    def test_embed_different_texts_different_vectors(self) -> None:
        a = DeterministicHashBackend().embed("alpha beta")
        b = DeterministicHashBackend().embed("gamma delta")
        assert a != b

    def test_embed_empty_string_zero_vector(self) -> None:
        vec = DeterministicHashBackend().embed("")
        assert all(x == 0.0 for x in vec)

    def test_embed_l2_normalised(self) -> None:
        import math

        vec = DeterministicHashBackend().embed("hello world another")
        norm = math.sqrt(sum(x * x for x in vec))
        assert abs(norm - 1.0) < 1e-9


# ---------------------------------------------------------------------------
# EmbedderComponent
# ---------------------------------------------------------------------------
class TestEmbedderComponent:
    def test_marked_as_component(self) -> None:
        comp = EmbedderComponent(DeterministicHashBackend())
        assert is_component(comp)

    def test_declares_inputs(self) -> None:
        inputs = get_input_sockets(EmbedderComponent)
        assert set(inputs) == {"text"}

    def test_declares_outputs(self) -> None:
        outputs = get_output_sockets(EmbedderComponent)
        assert set(outputs) == {"embedding", "dimension"}

    def test_concurrency_group_io_bound(self) -> None:
        assert EmbedderComponent.concurrency_group == "io_bound"

    def test_run_returns_expected_shape(self) -> None:
        comp = EmbedderComponent(DeterministicHashBackend(dimension=32))
        out = comp.run(text="hello world")
        assert set(out) == {"embedding", "dimension"}
        assert isinstance(out["embedding"], tuple)
        assert out["dimension"] == 32
        assert len(out["embedding"]) == 32

    def test_run_rejects_non_string(self) -> None:
        comp = EmbedderComponent(DeterministicHashBackend())
        with pytest.raises(TypeError):
            comp.run(text=123)  # type: ignore[arg-type]

    def test_run_empty_text_still_returns_vector(self) -> None:
        comp = EmbedderComponent(DeterministicHashBackend(dimension=8))
        out = comp.run(text="")
        assert len(out["embedding"]) == 8

    def test_backend_name_exposed(self) -> None:
        comp = EmbedderComponent(DeterministicHashBackend())
        assert comp.backend_name == "deterministic"

    def test_dimension_property(self) -> None:
        comp = EmbedderComponent(DeterministicHashBackend(dimension=16))
        assert comp.dimension == 16

    def test_run_async_matches_sync(self) -> None:
        comp = EmbedderComponent(DeterministicHashBackend(dimension=32))
        sync_out = comp.run(text="hello async")
        async_out = asyncio.run(comp.run_async(text="hello async"))
        assert sync_out["embedding"] == async_out["embedding"]
        assert sync_out["dimension"] == async_out["dimension"]


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------
class TestBuildEmbedderFromConfig:
    def test_deterministic_backend(self) -> None:
        comp = build_embedder_from_config(_StubConfig(embedder="deterministic"))
        assert isinstance(comp, EmbedderComponent)
        assert comp.backend_name == "deterministic"

    def test_unknown_backend_falls_back(self, caplog: pytest.LogCaptureFixture) -> None:
        comp = build_embedder_from_config(_StubConfig(embedder="not_a_backend"))
        assert comp.backend_name == "deterministic"
        assert any("Unknown embedder backend" in rec.message for rec in caplog.records)

    def test_sentence_transformers_fallback_when_missing(self) -> None:
        # sentence-transformers is an optional dep; if absent the factory
        # must not crash — it falls back to deterministic.
        comp = build_embedder_from_config(_StubConfig(embedder="sentence_transformers"))
        assert isinstance(comp, EmbedderComponent)
        # Either real ST or the deterministic fallback; both are valid.
        assert comp.backend_name in {"sentence_transformers", "deterministic"}

    def test_onnx_backend_fallback_when_missing(self) -> None:
        comp = build_embedder_from_config(_StubConfig(embedder="onnx"))
        assert isinstance(comp, EmbedderComponent)

    def test_anthropic_always_falls_back(self) -> None:
        # Anthropic doesn't ship embeddings; the factory degrades.
        comp = build_embedder_from_config(_StubConfig(embedder="anthropic"))
        assert comp.backend_name == "deterministic"


# ---------------------------------------------------------------------------
# text_sha256_prefix helper
# ---------------------------------------------------------------------------
class TestSha256Prefix:
    def test_default_prefix_length(self) -> None:
        digest = text_sha256_prefix("hello")
        assert len(digest) == 12
        assert digest == hashlib.sha256(b"hello").hexdigest()[:12]

    def test_custom_length(self) -> None:
        assert len(text_sha256_prefix("x", length=4)) == 4

    def test_stable_across_calls(self) -> None:
        assert text_sha256_prefix("foo") == text_sha256_prefix("foo")
