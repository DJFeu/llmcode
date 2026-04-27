"""Local rerank backend — sentence-transformers cross-encoder.

Uses ``cross-encoder/ms-marco-MiniLM-L-6-v2`` (the same model the v12
memory subsystem reranker references). Loaded lazily once per process
into a module-level cache so successive ``rerank()`` calls reuse the
hot model.

Free, no API key, runs on CPU. Requires the ``[memory]`` extra
(``sentence-transformers``); without it the constructor raises an
ImportError pointing at the install command — silent fallback would
hide a misconfiguration that costs the user search quality.

Reference:
* https://www.sbert.net/docs/cross_encoder/usage/usage.html
* docs/superpowers/specs/2026-04-27-llm-code-v17-rag-pipeline-design.md §3.1
"""
from __future__ import annotations

import logging
import threading
from typing import Any

from llm_code.tools.rerank import RerankResult

_logger = logging.getLogger(__name__)

# Same model the v12 memory subsystem references. Locking the choice
# in v2.8.0; alternatives noted but not configurable per spec §10.
_DEFAULT_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"

# Module-level singleton — sentence-transformers model load is ~80MB
# disk + warm-up time, so we cache it across all backend instances in
# the process. Lock prevents two concurrent initial calls from both
# loading the model.
_model_cache: dict[str, Any] = {}
_model_lock = threading.Lock()


def _load_cross_encoder(model_name: str) -> Any:
    """Lazy-load the ``CrossEncoder`` model, caching across calls.

    Raises:
        ImportError: ``sentence-transformers`` is not installed.
    """
    cached = _model_cache.get(model_name)
    if cached is not None:
        return cached
    with _model_lock:
        cached = _model_cache.get(model_name)
        if cached is not None:
            return cached
        try:
            # Lazy import — sentence-transformers cold-start is heavy
            # and only paid when the user actually picks this backend.
            from sentence_transformers import CrossEncoder  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - exercised when extra missing
            raise ImportError(
                "install llmcode-cli[memory] to use the local rerank backend"
            ) from exc
        _logger.info("loading local rerank model %r (first use, ~80MB)", model_name)
        model = CrossEncoder(model_name)
        _model_cache[model_name] = model
        return model


class LocalRerankBackend:
    """Cross-encoder rerank backend.

    Uses ``sentence-transformers/ms-marco-MiniLM-L-6-v2`` to score
    each ``(query, document)`` pair, then returns the documents sorted
    by score-descending.

    Model is lazy-loaded on first ``rerank()`` call; subsequent calls
    reuse the cached instance. Without ``[memory]`` installed the
    constructor still succeeds (lazy init) but the first ``rerank()``
    call raises ``ImportError`` with the install command.
    """

    def __init__(self, model_name: str = _DEFAULT_MODEL) -> None:
        self._model_name = model_name

    @property
    def name(self) -> str:
        return "local"

    def rerank(
        self,
        query: str,
        documents: tuple[str, ...],
        top_k: int = 5,
    ) -> tuple[RerankResult, ...]:
        if not documents:
            return ()

        model = _load_cross_encoder(self._model_name)
        # CrossEncoder.predict expects a list of (query, doc) pairs
        # and returns a numpy array of scores. Sentence-transformers
        # 2.7+ also accepts a tuple but list is the documented shape.
        pairs = [(query, doc) for doc in documents]
        scores = model.predict(pairs)
        # Pair each score with its original index so ``original_index``
        # survives the sort.
        scored = [
            RerankResult(document=doc, score=float(score), original_index=i)
            for i, (doc, score) in enumerate(zip(documents, scores))
        ]
        scored.sort(key=lambda r: r.score, reverse=True)
        cap = max(0, min(int(top_k), len(scored)))
        return tuple(scored[:cap])
