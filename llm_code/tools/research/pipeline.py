"""Research pipeline orchestrator (v2.8.0 M5).

Implements the full RAG pipeline:

    expand → search × N (parallel) → fetch top-K (parallel) → rerank → top-3

Inputs and outputs are dependency-injected so the orchestrator can be
unit-tested without booting the entire runtime — the M5 plan §E.1
called this out explicitly.

Linkup short-circuit
--------------------

If ``profile.linkup_default_mode == "sourcedAnswer"`` AND a
``LinkupBackend`` is reachable in the search chain AND the backend is
healthy, the pipeline emits Linkup's sourced answer + sources
directly, skipping the per-URL fetch + rerank steps. This is much
faster on factual queries and Linkup's hosted model already does the
RAG step we'd run locally.

Per-step failure handling
-------------------------

Any per-task exception in the search / fetch ``gather`` calls is
LOGGED + CONTINUED — we never fail the whole pipeline because one
backend or one URL went down. The reranker sees the surviving
documents.

Plan: docs/superpowers/plans/2026-04-27-llm-code-v17-m5-research-tool.md
Spec: docs/superpowers/specs/2026-04-27-llm-code-v17-rag-pipeline-design.md §3.5
"""
from __future__ import annotations

import asyncio
import dataclasses
import logging
import time
from typing import Any, Awaitable, Callable

from llm_code.tools.rerank import (
    IdentityRerankBackend,
    RerankBackend,
    RerankResult,
    create_rerank_backend,
)
from llm_code.tools.research.expansion import expand
from llm_code.tools.search_backends import SearchResult
from llm_code.tools.search_backends import health as _health
from llm_code.tools.search_backends.linkup import (
    LinkupBackend,
    SourcedAnswer,
)

logger = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class ResearchSource:
    """A single source surfaced by the research pipeline."""

    title: str
    url: str
    snippet: str
    score: float = 0.0


@dataclasses.dataclass(frozen=True)
class ResearchOutput:
    """Result of a full research pipeline run.

    Attributes:
        query: Original user query.
        sources: Ranked sources (post-rerank when applicable).
        markdown: Pre-formatted markdown bundle the LLM can read.
        took_seconds: Wall-clock duration in seconds.
        backend: ``"pipeline"`` for the standard RAG path or
            ``"linkup_sourced"`` when the Linkup short-circuit fired.
        answer: Optional model-grounded answer (only set when
            ``backend == "linkup_sourced"``).
    """

    query: str
    sources: tuple[ResearchSource, ...]
    markdown: str
    took_seconds: float
    backend: str = "pipeline"
    answer: str = ""


# Type aliases for the injected dependencies.
SearchFn = Callable[[str, int], Awaitable[tuple[SearchResult, ...]]]
FetchFn = Callable[[str], Awaitable[str]]


def _depth_settings(depth: str) -> tuple[int, int, bool]:
    """Return ``(max_subqueries, top_k_fetch, do_rerank)`` for a depth.

    ``"fast"``     → 1 sub-query, K=3, no rerank
    ``"standard"`` → 3 sub-queries, K=5, rerank
    ``"deep"``     → 3 sub-queries, K=10, rerank
    """
    if depth == "fast":
        return 1, 3, False
    if depth == "deep":
        return 3, 10, True
    # Default + safe fallback.
    return 3, 5, True


def _format_markdown(query: str, sources: tuple[ResearchSource, ...]) -> str:
    """Format ranked sources as a markdown bundle."""
    if not sources:
        return f"## Research results for {query!r}\n\n(0 sources)\n"
    lines = [f"## Research results for {query!r}", ""]
    for i, src in enumerate(sources, start=1):
        lines.append(f"### {i}. {src.title or src.url}")
        lines.append(f"<{src.url}>")
        if src.score:
            lines.append(f"_score: {src.score:.3f}_")
        if src.snippet:
            lines.append("")
            lines.append(src.snippet)
        lines.append("")
        lines.append("---")
        lines.append("")
    lines.append(f"({len(sources)} sources)")
    return "\n".join(lines)


def _format_sourced_answer_markdown(query: str, answer: SourcedAnswer) -> str:
    """Format a Linkup sourced-answer response as markdown."""
    lines = [f"## Research answer for {query!r}", ""]
    if answer.answer:
        lines.append(answer.answer)
        lines.append("")
    if answer.sources:
        lines.append("### Sources")
        lines.append("")
        for i, s in enumerate(answer.sources, start=1):
            lines.append(f"{i}. **[{s.title or s.url}]({s.url})**")
            if s.snippet:
                lines.append(f"   {s.snippet}")
            lines.append("")
    else:
        lines.append("(no citation sources returned)")
    return "\n".join(lines)


def _try_linkup_short_circuit(
    profile: Any,
    search_chain: tuple[Any, ...],
) -> LinkupBackend | None:
    """Return a healthy Linkup backend if the short-circuit applies.

    Returns ``None`` if any condition fails — caller falls through to
    the standard pipeline.
    """
    mode = getattr(profile, "linkup_default_mode", "searchResults")
    if mode != "sourcedAnswer":
        return None
    for backend in search_chain:
        if isinstance(backend, LinkupBackend):
            if _health.is_healthy("linkup"):
                return backend
            logger.info(
                "Linkup short-circuit skipped: backend marked unhealthy",
            )
            return None
    return None


async def _bounded(
    semaphore: asyncio.Semaphore,
    coro: Awaitable[Any],
) -> Any:
    """Run ``coro`` under the semaphore."""
    async with semaphore:
        return await coro


async def _run_searches(
    sub_queries: tuple[str, ...],
    search_fn: SearchFn,
    *,
    per_query_max: int,
    semaphore: asyncio.Semaphore,
) -> tuple[SearchResult, ...]:
    """Run ``search_fn`` against each sub-query, dedupe by URL.

    Per-task exceptions log + continue; the union is built from
    surviving results. Order preserves first-seen URLs (stable across
    runs given fixed inputs).
    """
    tasks = [
        _bounded(semaphore, search_fn(sq, per_query_max))
        for sq in sub_queries
    ]
    raw_results = await asyncio.gather(*tasks, return_exceptions=True)

    seen: set[str] = set()
    out: list[SearchResult] = []
    for sq, result in zip(sub_queries, raw_results):
        if isinstance(result, BaseException):
            logger.info("research search failed for %r: %s", sq, result)
            continue
        for r in result:
            if not r.url or r.url in seen:
                continue
            seen.add(r.url)
            out.append(r)
    return tuple(out)


async def _run_fetches(
    urls: tuple[str, ...],
    fetch_fn: FetchFn,
    *,
    semaphore: asyncio.Semaphore,
) -> tuple[tuple[str, str], ...]:
    """Fetch each URL via ``fetch_fn`` returning ``(url, body)`` pairs.

    Per-task exceptions log + continue.
    """
    tasks = [_bounded(semaphore, fetch_fn(url)) for url in urls]
    bodies = await asyncio.gather(*tasks, return_exceptions=True)

    out: list[tuple[str, str]] = []
    for url, body in zip(urls, bodies):
        if isinstance(body, BaseException):
            logger.info("research fetch failed for %s: %s", url, body)
            continue
        if not body:
            continue
        text = body if isinstance(body, str) else str(body)
        out.append((url, text))
    return tuple(out)


def _resolve_rerank_backend(profile: Any) -> RerankBackend:
    """Build the rerank backend declared by the profile."""
    name = getattr(profile, "rerank_backend", "local") or "local"
    try:
        return create_rerank_backend(name)
    except (ValueError, ImportError) as exc:
        logger.warning(
            "rerank backend %r unavailable (%s); using identity passthrough",
            name, exc,
        )
        return IdentityRerankBackend()


async def run_research(
    query: str,
    *,
    profile: Any,
    search_chain: tuple[Any, ...],
    search_fn: SearchFn,
    fetch_fn: FetchFn,
    rerank: RerankBackend | None = None,
    expansion_provider: Any = None,
    expansion_model: str | None = None,
    depth: str = "standard",
    max_results: int = 3,
) -> ResearchOutput:
    """Run the full research pipeline end-to-end.

    Args:
        query: User query.
        profile: ``ModelProfile`` instance (or compatible duck-type).
        search_chain: Iterable of search-backend instances. Inspected
            for the Linkup short-circuit; the actual backend walk is
            owned by ``search_fn`` (the dependency-injection seam).
        search_fn: Async callable ``(query, max_results) -> tuple[SearchResult]``.
            Typically wraps ``WebSearchTool._search_with_fallback`` to
            reuse the v2.8.0 M4 health-aware ordering.
        fetch_fn: Async callable ``(url) -> str`` returning page
            markdown. Typically wraps Jina Reader (v2.7.0a1).
        rerank: Optional ``RerankBackend``. When ``None``, resolved
            from ``profile.rerank_backend``.
        expansion_provider: Optional LLM provider for ``"llm"``
            expansion mode. Ignored for ``"template"`` / ``"off"``.
        expansion_model: Optional model override for the expansion
            provider call.
        depth: ``"fast"`` / ``"standard"`` / ``"deep"``.
        max_results: Final source count (after rerank, if applicable).

    Returns:
        :class:`ResearchOutput` with markdown bundle, ranked sources,
        and timing.
    """
    started = time.monotonic()

    # ── Linkup short-circuit ────────────────────────────────────────
    linkup_backend = _try_linkup_short_circuit(profile, tuple(search_chain))
    if linkup_backend is not None:
        try:
            sourced = await asyncio.to_thread(
                linkup_backend.sourced_answer, query, depth=depth,
            )
            _health.record_success("linkup")
        except Exception as exc:
            logger.info(
                "Linkup sourced_answer failed (%s); falling back to pipeline", exc,
            )
            _health.record_failure("linkup", kind="error")
        else:
            sources = tuple(
                ResearchSource(
                    title=s.title,
                    url=s.url,
                    snippet=s.snippet,
                )
                for s in sourced.sources
            )
            md = _format_sourced_answer_markdown(query, sourced)
            return ResearchOutput(
                query=query,
                sources=sources,
                markdown=md,
                took_seconds=time.monotonic() - started,
                backend="linkup_sourced",
                answer=sourced.answer,
            )

    # ── Standard pipeline ───────────────────────────────────────────
    max_subqueries, top_k_fetch, do_rerank = _depth_settings(depth)
    # Profile cap may override depth's defaults if profile is tighter.
    profile_cap = int(getattr(profile, "research_max_subqueries", max_subqueries))
    max_subqueries = min(max_subqueries, profile_cap) if profile_cap > 0 else max_subqueries

    sub_queries = await expand(
        query, profile,
        provider=expansion_provider,
        model=expansion_model,
    )
    sub_queries = sub_queries[:max_subqueries] if max_subqueries > 0 else (query,)

    # Concurrency cap — semaphore size from profile or fallback to 5.
    cap = int(getattr(profile, "research_max_concurrency", 5)) or 5
    semaphore = asyncio.Semaphore(cap)

    # Per-sub-query search pull. Use a small per-query result cap so
    # the union doesn't balloon on `deep` depth — reranker handles
    # signal-vs-noise from there.
    per_query_max = max(top_k_fetch, max_results)
    candidates = await _run_searches(
        sub_queries, search_fn,
        per_query_max=per_query_max,
        semaphore=semaphore,
    )
    if not candidates:
        return ResearchOutput(
            query=query,
            sources=(),
            markdown=_format_markdown(query, ()),
            took_seconds=time.monotonic() - started,
            backend="pipeline",
        )

    # Fetch top-K URLs (already deduped by URL).
    top_urls = tuple(c.url for c in candidates[:top_k_fetch])
    fetched = await _run_fetches(top_urls, fetch_fn, semaphore=semaphore)
    fetched_by_url = {url: body for url, body in fetched}

    # Map URL → SearchResult so we can carry titles + snippets through
    # to the ranked output.
    by_url = {c.url: c for c in candidates}

    # Build the document list for reranking. Use the fetched body when
    # available, otherwise fall back to the search snippet so a backend
    # that returned snippet-only results still has signal.
    doc_pairs: list[tuple[str, str, str, str]] = []  # (url, title, snippet, doc_text)
    for url in top_urls:
        c = by_url.get(url)
        if c is None:
            continue
        doc_text = fetched_by_url.get(url) or c.snippet or c.title or url
        doc_pairs.append((url, c.title, c.snippet, doc_text))

    if not doc_pairs:
        return ResearchOutput(
            query=query,
            sources=(),
            markdown=_format_markdown(query, ()),
            took_seconds=time.monotonic() - started,
            backend="pipeline",
        )

    # Cap each doc at 50K chars per spec §3.5 step 4.
    docs_for_rerank = tuple(d[3][:50_000] for d in doc_pairs)

    if do_rerank:
        active_rerank = rerank if rerank is not None else _resolve_rerank_backend(profile)
        try:
            rerank_results = active_rerank.rerank(
                query, docs_for_rerank, top_k=max_results,
            )
        except Exception as exc:
            logger.info(
                "rerank failed (%s); using search-native order", exc,
            )
            rerank_results = tuple(
                RerankResult(document=docs_for_rerank[i], score=1.0 - 0.01 * i, original_index=i)
                for i in range(min(max_results, len(docs_for_rerank)))
            )
    else:
        rerank_results = tuple(
            RerankResult(document=docs_for_rerank[i], score=1.0 - 0.01 * i, original_index=i)
            for i in range(min(max_results, len(docs_for_rerank)))
        )

    sources: list[ResearchSource] = []
    for r in rerank_results:
        url, title, snippet, _doc_text = doc_pairs[r.original_index]
        # Prefer the rerank-source body (could be longer than snippet)
        # for the displayed excerpt; cap to keep the markdown bundle
        # readable.
        body_excerpt = (fetched_by_url.get(url) or snippet or "")[:1000]
        sources.append(
            ResearchSource(
                title=title,
                url=url,
                snippet=body_excerpt,
                score=r.score,
            )
        )

    return ResearchOutput(
        query=query,
        sources=tuple(sources),
        markdown=_format_markdown(query, tuple(sources)),
        took_seconds=time.monotonic() - started,
        backend="pipeline",
    )
