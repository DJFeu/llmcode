"""Rerank backends for the v2.8.0 RAG pipeline.

Reranking takes a query plus a set of candidate documents (passages,
search-result snippets, fetched-page bodies) and returns them sorted
by semantic relevance to the query — typically much better signal
than a keyword search engine's native ranking, especially on
heterogeneous candidate pools.

Three backends ship in v2.8.0:

* ``LocalRerankBackend`` — ``sentence-transformers/ms-marco-MiniLM-L-6-v2``
  cross-encoder. Default. Free, no key, runs on CPU. Lazy-loads the
  model once per process. Requires the ``[memory]`` extra.
* ``CohereRerankBackend`` — ``rerank-multilingual-v3.0`` via the
  Cohere REST API. Free tier 1000/mo. ``COHERE_API_KEY`` env var.
* ``JinaRerankBackend`` — ``jina-reranker-v2-base-multilingual`` via
  the Jina REST API. Anonymous tier supported (rate-limited);
  ``JINA_API_KEY`` raises the limit.
* ``IdentityRerankBackend`` — passthrough used when
  ``profile.rerank_backend == "none"``. Returns the input documents
  unchanged so callers can wire this Protocol into a code path that
  always invokes ``rerank()`` without branching on availability.

The factory :func:`create_rerank_backend` resolves a name string to
a concrete backend instance.

Plan: docs/superpowers/plans/2026-04-27-llm-code-v17-m1-rerank-backends.md
Spec: docs/superpowers/specs/2026-04-27-llm-code-v17-rag-pipeline-design.md §3.1
"""
from __future__ import annotations

import dataclasses
import os
from typing import Protocol, runtime_checkable

__all__ = [
    "RerankResult",
    "RerankBackend",
    "RateLimitError",
    "AuthError",
    "IdentityRerankBackend",
    "create_rerank_backend",
]


@dataclasses.dataclass(frozen=True)
class RerankResult:
    """A single reranked document.

    ``original_index`` lets callers map the reranked output back to
    auxiliary state (URLs, titles) tracked alongside the document
    text — the rerank Protocol takes only ``(query, documents)`` so
    the backends remain stateless.
    """

    document: str
    score: float
    original_index: int


class RateLimitError(Exception):
    """Raised when a rerank backend is rate-limited (HTTP 429)."""


class AuthError(Exception):
    """Raised when a rerank backend rejects auth (HTTP 401 / 403).

    Distinct from ``RateLimitError`` so the caller knows whether to
    retry-after or give up entirely.
    """


@runtime_checkable
class RerankBackend(Protocol):
    """Protocol for rerank backends.

    Implementations MUST be free of module-level state — backends are
    instantiated by the factory once per profile and may be invoked
    concurrently (e.g. from the M5 research pipeline).
    """

    @property
    def name(self) -> str:
        """Backend identifier — ``"local"`` / ``"cohere"`` / ``"jina"`` / ``"none"``."""
        ...

    def rerank(
        self,
        query: str,
        documents: tuple[str, ...],
        top_k: int = 5,
    ) -> tuple[RerankResult, ...]:
        """Rerank ``documents`` by semantic relevance to ``query``.

        Args:
            query: User query.
            documents: Candidate documents, in their search-engine native
                order. Empty tuple → empty tuple is a valid no-op.
            top_k: Maximum number of results to return. The backend MAY
                return fewer if it has fewer candidates; MUST NOT return
                more.

        Returns:
            Tuple of :class:`RerankResult` sorted score-descending. The
            ``original_index`` of each result points back into the input
            ``documents`` tuple.

        Raises:
            RateLimitError: Backend signalled rate-limit (HTTP 429 or
                equivalent). Caller may retry against another backend.
            AuthError: Backend rejected auth (HTTP 401 / 403 / missing
                key). Caller should fall back to a different backend or
                surface the misconfiguration.
        """
        ...


# ---------------------------------------------------------------------------
# Identity backend — passthrough used when ``profile.rerank_backend == "none"``
# ---------------------------------------------------------------------------


class IdentityRerankBackend:
    """Passthrough rerank backend.

    Returns the first ``top_k`` documents unchanged with placeholder
    scores. Used when ``profile.rerank_backend == "none"`` so callers
    never have to branch on "is reranking enabled?" — they always
    invoke ``rerank()`` and trust the backend to do the right thing.
    """

    @property
    def name(self) -> str:
        return "none"

    def rerank(
        self,
        query: str,  # noqa: ARG002 — Protocol contract
        documents: tuple[str, ...],
        top_k: int = 5,
    ) -> tuple[RerankResult, ...]:
        cap = max(0, min(int(top_k), len(documents)))
        # Synthesise a monotone-descending score so callers that sort
        # by score get a stable order matching the input.
        return tuple(
            RerankResult(document=doc, score=max(0.0, 1.0 - 0.01 * i), original_index=i)
            for i, doc in enumerate(documents[:cap])
        )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_rerank_backend(
    name: str,
    *,
    api_key: str | None = None,
) -> RerankBackend:
    """Resolve a rerank backend name to a concrete instance.

    Args:
        name: One of ``"local"``, ``"cohere"``, ``"jina"``, ``"none"``.
        api_key: Optional explicit API key for cloud backends. Cohere
            requires it; Jina accepts an empty key (anonymous tier).
            When ``None``, the factory pulls from the canonical env var
            (``COHERE_API_KEY`` / ``JINA_API_KEY``).

    Raises:
        ValueError: Unknown backend name.
    """
    if name == "none":
        return IdentityRerankBackend()
    if name == "local":
        # Lazy import — sentence-transformers is heavy and only needed
        # when the user actually picks the local backend.
        from llm_code.tools.rerank.local import LocalRerankBackend
        return LocalRerankBackend()
    if name == "cohere":
        from llm_code.tools.rerank.cohere import CohereRerankBackend
        key = api_key if api_key is not None else os.environ.get("COHERE_API_KEY", "")
        return CohereRerankBackend(api_key=key)
    if name == "jina":
        from llm_code.tools.rerank.jina import JinaRerankBackend
        # Jina anonymous tier works — empty string is fine.
        key = api_key if api_key is not None else os.environ.get("JINA_API_KEY", "")
        return JinaRerankBackend(api_key=key)
    raise ValueError(f"Unknown rerank backend: {name!r}")
