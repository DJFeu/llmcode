"""Jina rerank backend — ``jina-reranker-v2-base-multilingual``.

Free anonymous tier (rate-limited); ``JINA_API_KEY`` raises the limit.
Docs: https://api.jina.ai/redoc#tag/rerank

Request body shape (Jina rerank endpoint)::

    POST https://api.jina.ai/v1/rerank
    {
        "model": "jina-reranker-v2-base-multilingual",
        "query": "<query>",
        "documents": ["doc1", "doc2", ...],
        "top_n": <top_k>
    }

Response::

    {"results": [{"index": 0, "relevance_score": 0.91, "document": {"text": "..."}}, ...]}
"""
from __future__ import annotations

import httpx

from llm_code.tools.rerank import RateLimitError, RerankResult

_JINA_RERANK_URL = "https://api.jina.ai/v1/rerank"
_JINA_MODEL = "jina-reranker-v2-base-multilingual"


class JinaRerankBackend:
    """Jina rerank backend.

    Free anonymous tier; ``JINA_API_KEY`` raises the rate limit.
    Docs: https://api.jina.ai/redoc#tag/rerank
    """

    def __init__(self, api_key: str = "") -> None:
        """Initialise the backend.

        Args:
            api_key: Optional Jina API key. Empty / whitespace-only is
                accepted (anonymous tier works), the constructor only
                normalises the value.
        """
        self._api_key = api_key.strip() if api_key else ""

    @property
    def name(self) -> str:
        return "jina"

    def rerank(
        self,
        query: str,
        documents: tuple[str, ...],
        top_k: int = 5,
    ) -> tuple[RerankResult, ...]:
        if not documents:
            return ()

        cap = max(0, min(int(top_k), len(documents)))
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        try:
            response = httpx.post(
                _JINA_RERANK_URL,
                json={
                    "model": _JINA_MODEL,
                    "query": query,
                    "documents": list(documents),
                    "top_n": cap,
                },
                headers=headers,
                timeout=15.0,
            )
        except httpx.RequestError:
            return ()

        if response.status_code == 429:
            raise RateLimitError("Jina rerank rate limited (HTTP 429)")
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
        results.sort(key=lambda r: r.score, reverse=True)
        return tuple(results[:cap])
