"""ResearchTool — the high-level v2.8.0 RAG entry point.

One tool call runs:
    expand → search × N (parallel) → fetch top-K (parallel) → rerank → top-3

For ``research X`` / ``find papers about X`` / ``compare A vs B`` style
queries the LLM can call this once instead of three round-trips through
``web_search`` + ``web_fetch``.

Behaviour is profile-driven:
* ``profile.research_query_expansion`` — template / llm / off
* ``profile.research_default_depth`` — default depth (overridable per call)
* ``profile.research_max_subqueries`` — cap on expanded sub-queries
* ``profile.research_max_concurrency`` — async semaphore size
* ``profile.linkup_default_mode`` — short-circuit to Linkup sourced answer
* ``profile.rerank_backend`` — which rerank to use

Plan: docs/superpowers/plans/2026-04-27-llm-code-v17-m5-research-tool.md
"""
from __future__ import annotations

import logging
from typing import Literal

from pydantic import BaseModel, Field

from llm_code.tools.base import PermissionLevel, Tool, ToolResult

logger = logging.getLogger(__name__)


class ResearchInput(BaseModel):
    """Input schema for :class:`ResearchTool`."""

    query: str
    max_results: int = Field(default=3, ge=1, le=20)
    depth: Literal["fast", "standard", "deep"] = "standard"


class ResearchTool(Tool):
    """High-level research pipeline tool (v2.8.0 keystone).

    The LLM should prefer this over ``web_search`` / ``web_fetch`` for
    research-style queries.
    """

    is_async: bool = True

    @property
    def name(self) -> str:
        return "research"

    @property
    def description(self) -> str:
        return (
            "Run a multi-step research pipeline: expand the query into "
            "sub-queries (when the profile asks), fetch search results "
            "from multiple backends in parallel, extract page content "
            "with Jina Reader, rerank by relevance, and return the top "
            "results with full extracted markdown. "
            "Prefer this tool over web_search for any 'research X' / "
            "'find papers about X' / 'compare A vs B' style query."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The research question.",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Final source count after rerank (default 3).",
                    "default": 3,
                },
                "depth": {
                    "type": "string",
                    "enum": ["fast", "standard", "deep"],
                    "description": (
                        "Pipeline depth. 'fast' (1 sub-query, K=3, no rerank) "
                        "for quick lookups; 'standard' (3 sub-queries, K=5, "
                        "rerank) for typical research; 'deep' (3 sub-queries, "
                        "K=10, rerank) for thorough investigation."
                    ),
                    "default": "standard",
                },
            },
            "required": ["query"],
        }

    @property
    def required_permission(self) -> PermissionLevel:
        return PermissionLevel.READ_ONLY

    @property
    def input_model(self) -> type[ResearchInput]:
        return ResearchInput

    def is_read_only(self, args: dict) -> bool:  # noqa: ARG002
        return True

    def is_concurrency_safe(self, args: dict) -> bool:  # noqa: ARG002
        return True

    def execute(self, args: dict) -> ToolResult:
        """Sync wrapper — bridges to ``execute_async`` via ``asyncio.run``.

        The runtime engine prefers ``execute_async`` (we set
        ``is_async = True`` so the engine awaits it directly), so this
        path only fires when something calls the sync API explicitly
        (legacy code paths / direct test invocations).
        """
        import asyncio
        try:
            return asyncio.run(self.execute_async(args))
        except RuntimeError as exc:
            # Already inside an event loop — caller should use
            # execute_async. Surface a clear error.
            return ToolResult(
                output=(
                    "Error: research tool can't run sync inside an event loop "
                    f"({exc}). Use execute_async."
                ),
                is_error=True,
            )

    async def execute_async(self, args: dict) -> ToolResult:
        """Run the research pipeline asynchronously."""
        try:
            parsed = ResearchInput(**args)
        except Exception as exc:
            return ToolResult(
                output=f"Invalid input: {exc}",
                is_error=True,
            )

        # Resolve runtime collaborators.
        profile = self._resolve_profile()
        search_chain = self._build_search_chain()
        search_fn = self._make_search_fn(search_chain)
        fetch_fn = self._make_fetch_fn()

        depth = parsed.depth or getattr(profile, "research_default_depth", "standard")

        try:
            from llm_code.tools.research.pipeline import run_research
            output = await run_research(
                parsed.query,
                profile=profile,
                search_chain=tuple(search_chain),
                search_fn=search_fn,
                fetch_fn=fetch_fn,
                depth=depth,
                max_results=parsed.max_results,
            )
        except Exception as exc:
            return ToolResult(
                output=f"Error: research pipeline failed: {exc}",
                is_error=True,
            )

        return ToolResult(
            output=output.markdown,
            is_error=False,
            metadata={
                "query": output.query,
                "took_seconds": output.took_seconds,
                "backend": output.backend,
                "source_count": len(output.sources),
            },
        )

    # ------------------------------------------------------------------
    # Runtime collaborator helpers — kept tiny so unit tests can patch
    # them with deterministic doubles.
    # ------------------------------------------------------------------

    def _resolve_profile(self):
        """Resolve the active model profile.

        Falls back to a default ``ModelProfile`` when the runtime
        config is unreachable so the tool is always usable.
        """
        try:
            from llm_code.runtime.config import RuntimeConfig
            cfg = RuntimeConfig()
            model = cfg.model
            from llm_code.runtime.model_profile import ModelProfile, get_profile
            if model:
                return get_profile(model)
            return ModelProfile()
        except Exception:
            from llm_code.runtime.model_profile import ModelProfile
            return ModelProfile()

    def _build_search_chain(self) -> list:
        """Build the iterable of healthy search-backend instances.

        Mirrors the logic in ``WebSearchTool._search_with_fallback``
        but returns instances rather than ``(name, kwargs)`` tuples so
        the pipeline can introspect them (e.g. for the Linkup short-
        circuit).
        """
        chain: list = []
        try:
            from llm_code.runtime.config import WebSearchConfig
            cfg = WebSearchConfig()
        except Exception:
            cfg = None

        from llm_code.tools.search_backends import create_backend
        import os

        # 1. DuckDuckGo (always available).
        try:
            chain.append(create_backend("duckduckgo"))
        except Exception:
            pass

        if cfg is not None:
            for env_var, name in (
                (getattr(cfg, "brave_api_key_env", "BRAVE_API_KEY"), "brave"),
                (getattr(cfg, "exa_api_key_env", "EXA_API_KEY"), "exa"),
                (getattr(cfg, "linkup_api_key_env", "LINKUP_API_KEY"), "linkup"),
                (getattr(cfg, "tavily_api_key_env", "TAVILY_API_KEY"), "tavily"),
                (getattr(cfg, "serper_api_key_env", "SERPER_API_KEY"), "serper"),
            ):
                key = os.environ.get(env_var, "")
                if not key:
                    continue
                try:
                    chain.append(create_backend(name, api_key=key))
                except Exception as exc:
                    logger.info("research backend %s init failed: %s", name, exc)

            # Jina is special — anonymous tier.
            jina_key = os.environ.get(
                getattr(cfg, "jina_api_key_env", "JINA_API_KEY"), "",
            )
            try:
                chain.append(create_backend("jina", api_key=jina_key))
            except Exception:
                pass
        return chain

    def _make_search_fn(self, search_chain: list):
        """Return an async search callable that walks the chain.

        Each call delegates to backend.search() in order, returning the
        first non-empty result tuple. Health tracking is delegated to
        ``WebSearchTool._search_with_fallback`` semantics — exceptions
        get logged + recorded, not propagated.
        """
        from llm_code.tools.search_backends import RateLimitError
        from llm_code.tools.search_backends import health as _health
        import asyncio

        async def _search(query: str, max_results: int):
            ordered = list(search_chain)
            try:
                names = [b.name for b in ordered]
                ordered_names = _health.sort_chain(tuple(names))
                by_name = {b.name: b for b in ordered}
                ordered = [by_name[n] for n in ordered_names if n in by_name]
            except Exception:
                pass
            for backend in ordered:
                try:
                    results = await asyncio.to_thread(
                        backend.search, query, max_results=max_results,
                    )
                except RateLimitError:
                    _health.record_failure(backend.name, kind="rate_limit")
                    continue
                except Exception:
                    _health.record_failure(backend.name, kind="error")
                    continue
                _health.record_success(backend.name)
                if results:
                    return results
            return ()

        return _search

    def _make_fetch_fn(self):
        """Return an async fetcher that pulls page bodies via Jina Reader."""
        from llm_code.tools.web_fetch import (
            JinaReaderError,
            fetch_via_jina_reader_async,
        )

        async def _fetch(url: str) -> str:
            try:
                body, _content_type, _status = await fetch_via_jina_reader_async(url)
                return body
            except JinaReaderError as exc:
                logger.info("research fetch via Jina failed for %s: %s", url, exc)
                return ""
            except Exception as exc:
                logger.info("research fetch unexpected error for %s: %s", url, exc)
                return ""

        return _fetch
