"""Cohere rerank backend — ``rerank-multilingual-v3.0``.

Free tier: 1000 calls / month.
Docs: https://docs.cohere.com/reference/rerank

Auth header
-----------

``Authorization: Bearer <COHERE_API_KEY>`` per Cohere's docs.

Request body shape (rerank v2 endpoint)::

    POST https://api.cohere.com/v2/rerank
    {
        "model": "rerank-multilingual-v3.0",
        "query": "<query>",
        "documents": ["doc1", "doc2", ...],
        "top_n": <top_k>
    }

Response::

    {"results": [{"index": 0, "relevance_score": 0.87}, ...]}

The ``index`` field points back into the request's ``documents`` array
which is what we use to populate ``RerankResult.original_index``.
"""
from __future__ import annotations

import httpx

from llm_code.tools.rerank import AuthError, RateLimitError, RerankResult

_COHERE_RERANK_URL = "https://api.cohere.com/v2/rerank"
_COHERE_MODEL = "rerank-multilingual-v3.0"


class CohereRerankBackend:
    """Cohere rerank backend.

    Free tier: 1000 / month.
    Docs: https://docs.cohere.com/reference/rerank
    """

    def __init__(self, api_key: str) -> None:
        """Initialise the backend.

        Args:
            api_key: Cohere API key. Empty / whitespace-only key is
                accepted at construction (the factory pulls from an env
                var that may be unset) but raises :class:`AuthError`
                eagerly on the first ``rerank()`` call.
        """
        self._api_key = api_key.strip() if api_key else ""

    @property
    def name(self) -> str:
        return "cohere"

    def rerank(
        self,
        query: str,
        documents: tuple[str, ...],
        top_k: int = 5,
    ) -> tuple[RerankResult, ...]:
        if not documents:
            return ()
        if not self._api_key:
            raise AuthError(
                "Cohere API key not configured — set COHERE_API_KEY or pick a "
                "different rerank_backend",
            )

        cap = max(0, min(int(top_k), len(documents)))
        try:
            response = httpx.post(
                _COHERE_RERANK_URL,
                json={
                    "model": _COHERE_MODEL,
                    "query": query,
                    "documents": list(documents),
                    "top_n": cap,
                },
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                timeout=15.0,
            )
        except httpx.RequestError:
            return ()

        if response.status_code == 429:
            raise RateLimitError("Cohere rerank rate limited (HTTP 429)")
        if response.status_code in (401, 403):
            raise AuthError(
                "Cohere API authentication failed — check the COHERE_API_KEY "
                f"env var (HTTP {response.status_code})"
            )
        if response.status_code != 200:
            return ()

        try:
            data = response.json()
        except Exception:
            return ()

        raw_results = data.get("results") if isinstance(data, dict) else None
        if not isinstance(raw_results, list):
            return ()

        results: list[RerankResult] = []
        for r in raw_results:
            if not isinstance(r, dict):
                continue
            idx = r.get("index")
            if not isinstance(idx, int) or idx < 0 or idx >= len(documents):
                continue
            score = r.get("relevance_score")
            try:
                score_f = float(score) if score is not None else 0.0
            except (TypeError, ValueError):
                score_f = 0.0
            results.append(
                RerankResult(
                    document=documents[idx],
                    score=score_f,
                    original_index=idx,
                )
            )
        # Cohere returns results in score-descending order already, but
        # we sort defensively in case a future API tweak changes that.
        results.sort(key=lambda r: r.score, reverse=True)
        return tuple(results[:cap])
