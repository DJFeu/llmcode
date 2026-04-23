"""Tests for the ONNX embedder's 3-layer fallback chain.

The chain, from :func:`llm_code.engine.components.memory.embedder._build_onnx`:

1. ``LLMCODE_ONNX_MODEL_PATH`` env var (local file)
2. HuggingFace cache via ``huggingface_hub.hf_hub_download``
3. Deterministic hash safety net

Each layer must warn + fall through to the next on failure so the
pipeline never crashes mid-build. We exercise every fall-through
explicitly; the "real ONNX session success" path is not covered here
because it requires a ~22 MB model file + HF tokenizer stack.

Plan: pyproject extras refinement + ONNX asset bundling.
"""
from __future__ import annotations

import logging
import sys
import types
from pathlib import Path

import pytest

from llm_code.engine.components.memory.embedder import (
    DeterministicHashBackend,
    EmbedderComponent,
    build_embedder_from_config,
)


class _StubConfig:
    """Minimal MemoryConfig duck-type for factory tests."""

    def __init__(self, *, embedder: str = "onnx", embedder_model: str = "") -> None:
        self.embedder = embedder
        self.embedder_model = embedder_model


@pytest.fixture()
def _clear_onnx_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip the env override so each test starts from a clean slate."""
    monkeypatch.delenv("LLMCODE_ONNX_MODEL_PATH", raising=False)


@pytest.fixture()
def _drop_huggingface_hub(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force ``import huggingface_hub`` to fail so Layer 2 is inert.

    Uses ``sys.modules`` injection with ``None`` which makes Python
    raise :class:`ModuleNotFoundError` on the next import — cleaner than
    mutating ``sys.path`` and survives cached module refs from earlier
    tests.
    """
    monkeypatch.setitem(sys.modules, "huggingface_hub", None)


class TestLayer1EnvVarMissingFile:
    """Layer 1 — env var points at a non-existent path."""

    def test_missing_path_falls_back_to_deterministic(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
        tmp_path: Path,
        _drop_huggingface_hub: None,
    ) -> None:
        pytest.importorskip("onnxruntime")
        bogus = tmp_path / "does_not_exist.onnx"
        monkeypatch.setenv("LLMCODE_ONNX_MODEL_PATH", str(bogus))

        caplog.set_level(logging.WARNING)
        comp = build_embedder_from_config(_StubConfig())

        assert isinstance(comp, EmbedderComponent)
        assert comp.backend_name == "deterministic"
        messages = " ".join(rec.message for rec in caplog.records)
        assert "LLMCODE_ONNX_MODEL_PATH" in messages
        assert str(bogus) in messages


class TestLayer1EnvVarInvalidFile:
    """Layer 1 — env var points at a file that exists but isn't a valid
    ONNX model. ``onnxruntime.InferenceSession`` will raise; we must
    warn and fall through to the deterministic safety net."""

    def test_synthetic_1_byte_file_falls_back(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
        tmp_path: Path,
        _drop_huggingface_hub: None,
    ) -> None:
        pytest.importorskip("onnxruntime")
        synthetic = tmp_path / "synthetic.onnx"
        synthetic.write_bytes(b"\x00")  # 1-byte garbage — not valid ONNX
        monkeypatch.setenv("LLMCODE_ONNX_MODEL_PATH", str(synthetic))

        caplog.set_level(logging.WARNING)
        comp = build_embedder_from_config(_StubConfig())

        assert isinstance(comp, EmbedderComponent)
        assert comp.backend_name == "deterministic"
        messages = " ".join(rec.message for rec in caplog.records)
        assert "failed to open ONNX session" in messages
        assert str(synthetic) in messages


class TestOnnxRuntimeMissing:
    """Layer 0 guard — when ``onnxruntime`` is not importable, we warn
    and return the deterministic backend immediately.

    We simulate the missing dep by injecting ``None`` into
    ``sys.modules`` for ``onnxruntime``; this is the same idiom used
    elsewhere in the suite for guarding optional deps.
    """

    def test_missing_onnxruntime_falls_back(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
        _clear_onnx_env: None,
    ) -> None:
        # If onnxruntime is already imported, force a fresh ImportError.
        monkeypatch.setitem(sys.modules, "onnxruntime", None)

        caplog.set_level(logging.WARNING)
        comp = build_embedder_from_config(_StubConfig())

        assert comp.backend_name == "deterministic"
        messages = " ".join(rec.message for rec in caplog.records)
        assert "onnxruntime not installed" in messages


class TestLayer2HuggingFaceHubMissing:
    """Layer 2 — ``huggingface_hub`` not installed. Factory must still
    succeed; Layer 3 deterministic fallback kicks in."""

    def test_hf_missing_falls_back_gracefully(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
        _clear_onnx_env: None,
        _drop_huggingface_hub: None,
    ) -> None:
        pytest.importorskip("onnxruntime")

        # Bind caplog to the embedder logger directly; a parent-level
        # setLevel doesn't propagate if a prior test installed its own
        # handler on the specific logger.
        caplog.set_level(logging.INFO, logger="llm_code.engine.components.memory.embedder")
        comp = build_embedder_from_config(_StubConfig())

        assert isinstance(comp, EmbedderComponent)
        assert comp.backend_name == "deterministic"
        messages = " ".join(rec.message for rec in caplog.records)
        # Either the Layer 2 "huggingface_hub not installed" info log or
        # the Layer 3 "env var + HF cache both unavailable" warning (the
        # latter always fires when we reach deterministic); both mention
        # the missing cache explicitly.
        assert (
            "huggingface_hub" in messages
            or "HF cache both unavailable" in messages
        ), f"expected a hf-related log, got: {messages!r}"


class TestLayer2HuggingFaceHubFailure:
    """Layer 2 — ``huggingface_hub`` is installed but
    ``hf_hub_download`` raises (offline / 404 / auth). We warn and fall
    through to deterministic."""

    def test_hf_download_failure_falls_back(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
        _clear_onnx_env: None,
    ) -> None:
        pytest.importorskip("onnxruntime")

        def _raise(*args: object, **kwargs: object) -> None:
            raise RuntimeError("simulated offline / download error")

        fake_hub = types.ModuleType("huggingface_hub")
        fake_hub.hf_hub_download = _raise  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "huggingface_hub", fake_hub)

        caplog.set_level(logging.WARNING)
        comp = build_embedder_from_config(_StubConfig())

        assert comp.backend_name == "deterministic"
        messages = " ".join(rec.message for rec in caplog.records)
        assert "HF download failed" in messages
        assert "simulated offline" in messages


class TestDeterministicFallbackStaysValid:
    """Regression — whichever layer failed, the returned Component must
    still respond to ``run(text=...)`` with a properly shaped tuple."""

    def test_fallback_backend_still_embeds(
        self,
        monkeypatch: pytest.MonkeyPatch,
        _clear_onnx_env: None,
        _drop_huggingface_hub: None,
    ) -> None:
        comp = build_embedder_from_config(_StubConfig())
        out = comp.run(text="fallback sanity check")
        assert set(out) == {"embedding", "dimension"}
        assert isinstance(out["embedding"], tuple)
        assert len(out["embedding"]) == out["dimension"]
        # The safety-net backend should identify itself honestly so
        # observability surfaces the degradation.
        assert comp.backend_name == "deterministic"


class TestONNXBackendDimensionInference:
    """Dimension inference — when we *do* manage to build a real
    session, the wrapper reads the output tensor's last dimension.
    Here we feed a stub session to exercise the helper without needing
    a real ONNX file."""

    def test_dimension_from_output_shape(self) -> None:
        from llm_code.engine.components.memory.embedder import _ONNXBackend

        class _StubOutput:
            shape = (1, None, 768)  # batch, seq, hidden

        class _StubSession:
            def get_outputs(self) -> list[_StubOutput]:
                return [_StubOutput()]

        backend = _ONNXBackend(_StubSession(), "dummy-model")
        assert backend.dimension == 768
        assert backend.name == "onnx"

    def test_dimension_falls_back_to_default_on_bad_shape(self) -> None:
        from llm_code.engine.components.memory.embedder import _ONNXBackend

        class _StubOutput:
            shape: list[object] = []

        class _StubSession:
            def get_outputs(self) -> list[_StubOutput]:
                return [_StubOutput()]

        backend = _ONNXBackend(_StubSession(), "dummy-model")
        # Defaults to MiniLM's 384 when the session can't report a shape.
        assert backend.dimension == 384

    def test_dimension_defaults_when_get_outputs_raises(self) -> None:
        from llm_code.engine.components.memory.embedder import _ONNXBackend

        class _StubSession:
            def get_outputs(self) -> list[object]:
                raise RuntimeError("broken session")

        backend = _ONNXBackend(_StubSession(), "dummy-model")
        assert backend.dimension == 384


class TestDeterministicBackendInstanceOfEmbedderComponent:
    """Belt-and-braces: the factory always returns a Component, even
    through every fallback path — callers never see a raw backend."""

    def test_component_wrapping(
        self,
        _clear_onnx_env: None,
        _drop_huggingface_hub: None,
    ) -> None:
        comp = build_embedder_from_config(_StubConfig())
        assert isinstance(comp, EmbedderComponent)
        # The factory wraps whichever backend we ended up with.
        assert isinstance(comp._backend, DeterministicHashBackend)
