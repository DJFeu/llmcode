"""RerankTool — expose rerank backends as a standalone tool.

The :class:`~llm_code.tools.rerank.RerankBackend` Protocol powers M5's
research pipeline internally; this tool exposes the same capability as
a first-class LLM tool for advanced use ("here's a list of candidate
documents — rank them for relevance to my query").

The active backend is resolved from ``profile.rerank_backend`` (one of
``"local"``, ``"cohere"``, ``"jina"``, ``"none"``). v2.8.0 default is
``"local"`` (sentence-transformers cross-encoder; free, runs on CPU).

Plan: docs/superpowers/plans/2026-04-27-llm-code-v17-m1-rerank-backends.md §A.6
"""
from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from llm_code.tools.base import PermissionLevel, Tool, ToolResult
from llm_code.tools.rerank import (
    AuthError,
    RateLimitError,
    create_rerank_backend,
)

logger = logging.getLogger(__name__)


class RerankInput(BaseModel):
    """Input schema for :class:`RerankTool`."""

    query: str
    documents: list[str]
    top_k: int = Field(default=5, ge=1, le=100)


class RerankTool(Tool):
    """Rerank a list of candidate documents by relevance to a query.

    Uses ``profile.rerank_backend`` to pick the implementation. Outputs
    a markdown ranked list with each document's score so the LLM can
    decide which excerpts to read in full.
    """

    @property
    def name(self) -> str:
        return "rerank"

    @property
    def description(self) -> str:
        return (
            "Rerank a list of candidate documents by semantic relevance to "
            "a query. Returns a markdown ranked list ordered most-relevant "
            "first. Use this when you have multiple candidate passages or "
            "search snippets and want to focus on the most relevant ones."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The query to rerank documents against.",
                },
                "documents": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Candidate documents (passages, snippets) to rerank.",
                },
                "top_k": {
                    "type": "integer",
                    "description": "Maximum number of ranked results to return (default 5).",
                    "default": 5,
                },
            },
            "required": ["query", "documents"],
        }

    @property
    def required_permission(self) -> PermissionLevel:
        return PermissionLevel.READ_ONLY

    @property
    def input_model(self) -> type[RerankInput]:
        return RerankInput

    def is_read_only(self, args: dict) -> bool:  # noqa: ARG002
        return True

    def is_concurrency_safe(self, args: dict) -> bool:  # noqa: ARG002
        return True

    def _resolve_backend_name(self) -> str:
        """Pull the active rerank backend name from runtime config.

        Falls back to ``"local"`` if the runtime profile module is not
        available (e.g. tests that import the tool directly without
        booting the runtime).
        """
        try:
            from llm_code.runtime.config import RuntimeConfig
            cfg = RuntimeConfig()
            model = cfg.model
            if not model:
                return "local"
            from llm_code.runtime.model_profile import get_profile
            profile = get_profile(model)
            return profile.rerank_backend or "local"
        except Exception:
            return "local"

    def execute(self, args: dict) -> ToolResult:
        """Rerank documents and format the ranked list as markdown."""
        try:
            parsed = RerankInput(**args)
        except Exception as exc:
            return ToolResult(
                output=f"Invalid input: {exc}",
                is_error=True,
            )

        if not parsed.documents:
            return ToolResult(
                output="(0 documents to rerank)",
                is_error=False,
            )

        backend_name = self._resolve_backend_name()
        try:
            backend = create_rerank_backend(backend_name)
        except (ValueError, ImportError) as exc:
            return ToolResult(
                output=f"Error: rerank backend {backend_name!r} unavailable: {exc}",
                is_error=True,
            )

        try:
            results = backend.rerank(
                parsed.query,
                tuple(parsed.documents),
                top_k=parsed.top_k,
            )
        except RateLimitError as exc:
            return ToolResult(
                output=f"Error: rerank backend rate limited: {exc}",
                is_error=True,
            )
        except AuthError as exc:
            return ToolResult(
                output=f"Error: rerank backend auth failed: {exc}",
                is_error=True,
            )
        except Exception as exc:
            return ToolResult(
                output=f"Error: rerank failed: {exc}",
                is_error=True,
            )

        lines = [f"## Reranked results for {parsed.query!r} (backend: {backend_name})", ""]
        if not results:
            lines.append("(0 results)")
            return ToolResult(output="\n".join(lines), is_error=False)

        for rank, r in enumerate(results, start=1):
            preview = r.document[:200].replace("\n", " ")
            if len(r.document) > 200:
                preview = preview + "…"
            lines.append(f"{rank}. **score={r.score:.3f}** (orig idx {r.original_index})")
            lines.append(f"   {preview}")
            lines.append("")
        lines.append(f"({len(results)} results)")
        return ToolResult(output="\n".join(lines), is_error=False)
