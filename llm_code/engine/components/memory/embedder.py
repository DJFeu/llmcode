"""EmbedderComponent + backend protocol + factory (v12 M7 Task 7.2).

Wraps an :class:`EmbeddingBackend` as a :mod:`llm_code.engine.component`
Component exposing ``text: str`` → ``(embedding, dimension)``. The
backend abstraction gives us three interchangeable implementations:

- ``sentence_transformers`` (default, local; requires the heavy torch
  stack so imports are guarded).
- ``openai`` / ``anthropic`` — HTTP backends; imports are optional.
- ``onnx`` — ONNX-Runtime backed embedding; requires the ``[memory-rerank]``
  extra but will be reused for embeddings in a follow-up.
- ``deterministic`` — zero-dependency hash-based backend used by tests
  and as the safety-net fallback when every richer backend fails to
  import. Produces reproducible vectors so parity tests are stable.

The ``@traced_component`` decorator attaches an OpenTelemetry span when
the optional OTel dep is installed. Raw text is **never** placed in a
span attribute — only its length + SHA256 prefix.

Plan: docs/superpowers/plans/2026-04-21-llm-code-v12-memory-components.md
"""
from __future__ import annotations

import hashlib
import logging
import math
from typing import Any, Protocol, runtime_checkable

from llm_code.engine.component import component, output_types, state_writes
from llm_code.engine.tracing import traced_component

__all__ = [
    "DeterministicHashBackend",
    "EmbedderComponent",
    "EmbeddingBackend",
    "build_embedder_from_config",
]

_logger = logging.getLogger(__name__)
_DEFAULT_DIMENSION = 384  # matches ``all-MiniLM-L6-v2``
_DEFAULT_MODEL = "all-MiniLM-L6-v2"


# ---------------------------------------------------------------------------
# Backend protocol
# ---------------------------------------------------------------------------
@runtime_checkable
class EmbeddingBackend(Protocol):
    """Tiny contract — any backend that can ``embed(text)`` qualifies.

    Implementations expose a stable ``dimension`` property so the
    :class:`EmbedderComponent` can surface it on its output socket
    without an extra round-trip.
    """

    name: str

    @property
    def dimension(self) -> int: ...

    def embed(self, text: str) -> tuple[float, ...]: ...


# ---------------------------------------------------------------------------
# Reference backend (zero deps, deterministic, used as safety-net fallback)
# ---------------------------------------------------------------------------
class DeterministicHashBackend:
    """Hash-based embedding — stable across runs, no external deps.

    Splits ``text`` into whitespace tokens, hashes each into a bucket,
    accumulates L2-normalised counts. Strictly a fallback for
    environments without sentence-transformers; quality is poor, but
    the signature matches the richer backends so tests can swap in.
    """

    name = "deterministic"

    def __init__(self, dimension: int = _DEFAULT_DIMENSION) -> None:
        if dimension <= 0:
            raise ValueError(f"dimension must be positive, got {dimension}")
        self._dim = int(dimension)

    @property
    def dimension(self) -> int:
        return self._dim

    def embed(self, text: str) -> tuple[float, ...]:
        vec = [0.0] * self._dim
        if text:
            for token in text.split():
                h = hashlib.sha256(token.encode("utf-8")).digest()
                # Use the first 4 bytes as a bucket index; next byte as
                # sign to spread the vector across the dimension.
                bucket = int.from_bytes(h[:4], "big") % self._dim
                sign = 1.0 if h[4] & 1 else -1.0
                vec[bucket] += sign
        norm = math.sqrt(sum(x * x for x in vec))
        if norm == 0.0:
            return tuple(vec)
        return tuple(x / norm for x in vec)


# ---------------------------------------------------------------------------
# Component
# ---------------------------------------------------------------------------
@traced_component
@component
@output_types(embedding=tuple, dimension=int)
@state_writes("query_embedding")
class EmbedderComponent:
    """Embed ``text`` using the injected backend.

    The ``concurrency_group = "io_bound"`` class attribute is inspected
    by the future async scheduler (M5); today it is informational.
    """

    concurrency_group = "io_bound"

    def __init__(self, backend: EmbeddingBackend) -> None:
        self._backend = backend

    @property
    def backend_name(self) -> str:
        return self._backend.name

    @property
    def dimension(self) -> int:
        return self._backend.dimension

    def run(self, text: str) -> dict[str, Any]:
        """Return ``{"embedding": tuple, "dimension": int}``.

        Raises:
            TypeError: if ``text`` is not a ``str``.
        """
        if not isinstance(text, str):
            raise TypeError(
                f"EmbedderComponent expects str, got {type(text).__name__}"
            )
        embedding = self._backend.embed(text)
        return {
            "embedding": tuple(embedding),
            "dimension": self._backend.dimension,
        }

    async def run_async(self, text: str) -> dict[str, Any]:
        """Async variant — defers to ``asyncio.to_thread`` for sync backends."""
        import asyncio

        embed = getattr(self._backend, "embed_async", None)
        if callable(embed):
            result = await embed(text)
            return {
                "embedding": tuple(result),
                "dimension": self._backend.dimension,
            }
        result = await asyncio.to_thread(self._backend.embed, text)
        return {
            "embedding": tuple(result),
            "dimension": self._backend.dimension,
        }


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------
def build_embedder_from_config(config: Any) -> EmbedderComponent:
    """Resolve the configured backend and wrap it in a Component.

    ``config`` is a :class:`~llm_code.runtime.config.MemoryConfig` (or a
    duck-typed substitute exposing ``embedder`` and ``embedder_model``).
    Unknown backend names fall back to ``DeterministicHashBackend`` with
    a warning so the pipeline survives a typo in user config.
    """
    backend_name = getattr(config, "embedder", "sentence_transformers") or "sentence_transformers"
    model = getattr(config, "embedder_model", _DEFAULT_MODEL) or _DEFAULT_MODEL

    backend: EmbeddingBackend
    if backend_name == "sentence_transformers":
        backend = _build_sentence_transformers(model)
    elif backend_name == "openai":
        backend = _build_openai(model)
    elif backend_name == "anthropic":
        backend = _build_anthropic(model)
    elif backend_name == "onnx":
        backend = _build_onnx(model)
    elif backend_name == "deterministic":
        backend = DeterministicHashBackend()
    else:
        _logger.warning(
            "Unknown embedder backend %r; falling back to deterministic hash",
            backend_name,
        )
        backend = DeterministicHashBackend()

    return EmbedderComponent(backend)


# ---------------------------------------------------------------------------
# Backend builders — each guarded with try/except import.
# ---------------------------------------------------------------------------
def _build_sentence_transformers(model: str) -> EmbeddingBackend:
    """Construct a sentence-transformers backed embedder.

    Falls back to :class:`DeterministicHashBackend` if the optional
    ``sentence-transformers`` / ``torch`` dependency chain is missing.
    """
    try:  # pragma: no cover - optional dep probe
        from sentence_transformers import SentenceTransformer  # type: ignore[import-not-found]
    except Exception:  # pragma: no cover - optional dep fallback
        _logger.warning(
            "sentence-transformers not installed; using deterministic "
            "hash embedder (configure `memory.embedder = deterministic` "
            "to silence this warning)",
        )
        return DeterministicHashBackend()

    class _STBackend:
        name = "sentence_transformers"

        def __init__(self, model_name: str) -> None:
            self._model = SentenceTransformer(model_name)
            self._dim = int(self._model.get_sentence_embedding_dimension() or _DEFAULT_DIMENSION)

        @property
        def dimension(self) -> int:
            return self._dim

        def embed(self, text: str) -> tuple[float, ...]:
            vec = self._model.encode(text, normalize_embeddings=True)
            return tuple(float(x) for x in list(vec))

    return _STBackend(model)  # type: ignore[return-value]


def _build_openai(model: str) -> EmbeddingBackend:
    """OpenAI-compatible embedding backend.

    Uses the ``openai`` Python SDK if available. Dimension defaults to
    ``_DEFAULT_DIMENSION`` when the SDK can't report it (unusual; most
    OpenAI embedding models publish a stable size).
    """
    try:  # pragma: no cover - optional dep probe
        from openai import OpenAI  # type: ignore[import-not-found]
    except Exception:  # pragma: no cover - optional dep fallback
        _logger.warning("openai SDK not installed; using deterministic hash")
        return DeterministicHashBackend()

    class _OpenAIBackend:
        name = "openai"

        def __init__(self, model_name: str) -> None:
            self._client = OpenAI()
            self._model = model_name
            self._dim = _DEFAULT_DIMENSION

        @property
        def dimension(self) -> int:
            return self._dim

        def embed(self, text: str) -> tuple[float, ...]:
            resp = self._client.embeddings.create(model=self._model, input=text)
            vec = resp.data[0].embedding
            self._dim = len(vec)
            return tuple(float(x) for x in vec)

    return _OpenAIBackend(model)  # type: ignore[return-value]


def _build_anthropic(model: str) -> EmbeddingBackend:
    """Anthropic does not ship a first-party embedding endpoint; this
    stub exists so ``memory.embedder = anthropic`` degrades predictably
    rather than crashing the pipeline at build time."""
    _logger.warning(
        "Anthropic does not offer embeddings; falling back to deterministic",
    )
    return DeterministicHashBackend()


def _build_onnx(model: str) -> EmbeddingBackend:
    """ONNX-Runtime backed embedder — opt-in via the ``[memory-rerank]``
    extra which ships ``onnxruntime``.

    Model-file resolution is a 3-layer fallback chain:

    1. ``LLMCODE_ONNX_MODEL_PATH`` env var — an absolute path to a local
       ONNX model file. When set but the path is missing or the file
       cannot be opened as an ONNX session, we warn and fall through.
    2. HuggingFace cache — if the optional ``huggingface_hub`` package
       is installed, we ask it to materialise
       ``sentence-transformers/all-MiniLM-L6-v2`` (``onnx/model.onnx``)
       from the local cache or the network. Any failure (offline, 404,
       unsupported dep) warns and falls through.
    3. Deterministic hash — the zero-dep safety net. Quality is poor
       but every downstream assertion about the Component call surface
       still holds, so the pipeline stays alive.

    We never bundle a real ONNX model in the wheel because the MiniLM
    export alone is ~22 MB; operators opt in via env var or HF cache.
    """
    try:  # pragma: no cover - optional dep probe
        import onnxruntime  # type: ignore[import-not-found]
    except Exception:  # pragma: no cover - optional dep fallback
        _logger.warning(
            "onnxruntime not installed; using deterministic hash for 'onnx' "
            "embedder (install llmcode[memory-rerank] to enable)",
        )
        return DeterministicHashBackend()

    import os
    from pathlib import Path

    model_path: Path | None = None

    # ---- Layer 1 — LLMCODE_ONNX_MODEL_PATH ----
    env_path = os.environ.get("LLMCODE_ONNX_MODEL_PATH")
    if env_path:
        candidate = Path(env_path)
        if candidate.is_file():
            model_path = candidate
            _logger.info(
                "onnx embedder: using LLMCODE_ONNX_MODEL_PATH=%s", env_path,
            )
        else:
            _logger.warning(
                "onnx embedder: LLMCODE_ONNX_MODEL_PATH=%s is not a file; "
                "falling through to HuggingFace cache",
                env_path,
            )

    # ---- Layer 2 — HuggingFace cache ----
    if model_path is None:
        try:
            from huggingface_hub import hf_hub_download  # type: ignore[import-not-found]
        except Exception:
            _logger.info(
                "onnx embedder: huggingface_hub not installed; skipping "
                "auto-fetch layer",
            )
        else:
            try:
                fetched = hf_hub_download(
                    repo_id="sentence-transformers/all-MiniLM-L6-v2",
                    filename="onnx/model.onnx",
                )
                model_path = Path(fetched)
                _logger.info(
                    "onnx embedder: resolved HF cache path %s", model_path,
                )
            except Exception as exc:
                _logger.warning(
                    "onnx embedder: HF download failed (%s); falling through "
                    "to deterministic hash",
                    exc,
                )

    # ---- Attempt to load + wrap ----
    if model_path is not None:
        try:
            session = onnxruntime.InferenceSession(str(model_path))
        except Exception as exc:
            _logger.warning(
                "onnx embedder: failed to open ONNX session at %s: %s; "
                "falling back to deterministic hash",
                model_path,
                exc,
            )
        else:
            return _ONNXBackend(session, model)

    # ---- Layer 3 — deterministic safety net ----
    _logger.warning(
        "onnx embedder: no valid model resolved (env var + HF cache both "
        "unavailable); using deterministic hash",
    )
    return DeterministicHashBackend()


class _ONNXBackend:
    """Thin wrapper around an ``onnxruntime.InferenceSession``.

    A production-quality ONNX embedder also needs a tokenizer matching
    the model (e.g. the HF tokenizer for MiniLM). We don't pull that
    dependency in directly — callers who need real embeddings should
    install ``sentence-transformers`` and use
    ``memory.embedder = sentence_transformers``. This wrapper exists so
    the factory's "session creation succeeded" branch is well-typed and
    exposes a stable dimension read from the model's output shape.
    """

    name = "onnx"

    def __init__(self, session: Any, model_name: str) -> None:
        self._session = session
        self._model_name = model_name
        self._dim = self._infer_dimension(session)

    @staticmethod
    def _infer_dimension(session: Any) -> int:
        try:
            outputs = session.get_outputs()
        except Exception:
            return _DEFAULT_DIMENSION
        if not outputs:
            return _DEFAULT_DIMENSION
        shape = getattr(outputs[0], "shape", None) or []
        for value in reversed(list(shape)):
            if isinstance(value, int) and value > 0:
                return value
        return _DEFAULT_DIMENSION

    @property
    def dimension(self) -> int:
        return self._dim

    def embed(self, text: str) -> tuple[float, ...]:
        """Best-effort embed — delegates to an HF tokenizer when present,
        otherwise raises :class:`RuntimeError` with a clear hint.

        This path is intentionally not exercised by the default test
        suite because it requires the (large) ``transformers`` package
        plus a real model on disk. Operators who enable the ONNX
        backend in production install the HF stack themselves.
        """
        try:
            from transformers import AutoTokenizer  # type: ignore[import-not-found]
        except Exception as exc:  # pragma: no cover - heavy optional dep
            raise RuntimeError(
                "ONNX embedder requires the 'transformers' tokenizer for "
                "real embeddings; install sentence-transformers or set "
                "memory.embedder = deterministic"
            ) from exc

        tokenizer = AutoTokenizer.from_pretrained(self._model_name)
        encoded = tokenizer(
            text, padding=True, truncation=True, return_tensors="np"
        )
        outputs = self._session.run(
            None,
            {
                "input_ids": encoded["input_ids"],
                "attention_mask": encoded["attention_mask"],
            },
        )
        import numpy as np  # type: ignore[import-not-found]

        token_embeddings = outputs[0]
        mask = encoded["attention_mask"][..., None]
        pooled = (token_embeddings * mask).sum(axis=1) / mask.sum(
            axis=1
        ).clip(min=1)
        norm = np.linalg.norm(pooled, axis=1, keepdims=True)
        normalised = pooled / np.where(norm == 0, 1, norm)
        return tuple(float(x) for x in normalised[0])


# The SHA256 helper is exposed for tests that want to assert the same
# truncation the span recorder uses.
def text_sha256_prefix(text: str, *, length: int = 12) -> str:
    """Return the first ``length`` hex chars of SHA256(``text``).

    Used as the single source of truth so observability spans and test
    assertions agree on the digest format.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:length]
