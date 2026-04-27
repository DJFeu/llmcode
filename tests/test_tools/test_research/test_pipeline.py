"""Research pipeline orchestrator tests (v2.8.0 M5)."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from llm_code.runtime.model_profile import ModelProfile
from llm_code.tools.rerank import IdentityRerankBackend, RerankResult
from llm_code.tools.research.pipeline import (
    ResearchOutput,
    ResearchSource,
    _depth_settings,
    _format_markdown,
    run_research,
)
from llm_code.tools.search_backends import SearchResult
from llm_code.tools.search_backends import health as _health
from llm_code.tools.search_backends.linkup import (
    LinkupBackend,
    Source as LinkupSource,
    SourcedAnswer,
)


@pytest.fixture(autouse=True)
def _reset_health() -> None:
    _health._reset_for_tests()
    yield
    _health._reset_for_tests()


def _profile(**kwargs: object) -> ModelProfile:
    base = dict(
        rerank_backend="none",
        research_query_expansion="off",
        research_default_depth="standard",
        research_max_subqueries=3,
        research_max_concurrency=5,
        linkup_default_mode="searchResults",
    )
    base.update(kwargs)
    return ModelProfile(**base)


def _make_search_fn(results_per_query: dict) -> object:
    """Return an async search_fn that maps query → result tuple."""

    async def _search(query: str, max_results: int) -> tuple[SearchResult, ...]:
        return tuple(results_per_query.get(query, ()))[:max_results]

    return _search


def _make_fetch_fn(bodies_by_url: dict, *, fail_urls: tuple[str, ...] = ()) -> object:

    async def _fetch(url: str) -> str:
        if url in fail_urls:
            raise RuntimeError(f"simulated fetch failure for {url}")
        return bodies_by_url.get(url, "")

    return _fetch


class TestDepthSettings:
    def test_fast_returns_no_rerank(self) -> None:
        max_q, k, do_rerank = _depth_settings("fast")
        assert max_q == 1
        assert k == 3
        assert do_rerank is False

    def test_standard_returns_rerank(self) -> None:
        max_q, k, do_rerank = _depth_settings("standard")
        assert max_q == 3
        assert k == 5
        assert do_rerank is True

    def test_deep_returns_rerank_with_higher_k(self) -> None:
        max_q, k, do_rerank = _depth_settings("deep")
        assert max_q == 3
        assert k == 10
        assert do_rerank is True

    def test_unknown_depth_falls_back_to_standard(self) -> None:
        assert _depth_settings("unknown") == _depth_settings("standard")


class TestPipelineHappyPath:
    async def test_fast_pipeline_returns_results(self) -> None:
        results = [
            SearchResult(title="A", url="https://a.com", snippet="snippet A"),
            SearchResult(title="B", url="https://b.com", snippet="snippet B"),
            SearchResult(title="C", url="https://c.com", snippet="snippet C"),
        ]
        search_fn = _make_search_fn({"q": results})
        fetch_fn = _make_fetch_fn({
            "https://a.com": "body A " * 30,
            "https://b.com": "body B " * 30,
            "https://c.com": "body C " * 30,
        })
        out = await run_research(
            "q",
            profile=_profile(),
            search_chain=(),
            search_fn=search_fn,
            fetch_fn=fetch_fn,
            depth="fast",
            max_results=3,
        )
        assert isinstance(out, ResearchOutput)
        assert out.backend == "pipeline"
        assert len(out.sources) == 3
        assert out.markdown.startswith("## Research results for")

    async def test_standard_pipeline_with_identity_rerank(self) -> None:
        # Two sub-queries with overlapping URLs to exercise URL dedup.
        sub_a = (
            SearchResult(title="A", url="https://shared.com", snippet="A snip"),
            SearchResult(title="X", url="https://only-a.com", snippet="X snip"),
        )
        sub_b = (
            SearchResult(title="A", url="https://shared.com", snippet="A snip 2"),
            SearchResult(title="Y", url="https://only-b.com", snippet="Y snip"),
        )
        search_fn = _make_search_fn({
            "research X": sub_a,
            "X paper 2024": sub_b,
            "X tutorial": (),
        })
        fetch_fn = _make_fetch_fn({
            "https://shared.com": "shared body " * 30,
            "https://only-a.com": "only-a body " * 30,
            "https://only-b.com": "only-b body " * 30,
        })
        out = await run_research(
            "research X",
            profile=_profile(
                research_query_expansion="template",
                research_max_subqueries=3,
            ),
            search_chain=(),
            search_fn=search_fn,
            fetch_fn=fetch_fn,
            rerank=IdentityRerankBackend(),
            depth="standard",
            max_results=3,
        )
        assert out.backend == "pipeline"
        urls = {s.url for s in out.sources}
        # Shared URL was deduplicated.
        assert "https://shared.com" in urls
        assert len(out.sources) <= 3

    async def test_empty_search_returns_zero_sources_no_error(self) -> None:
        search_fn = _make_search_fn({})  # all queries empty
        fetch_fn = _make_fetch_fn({})
        out = await run_research(
            "q",
            profile=_profile(),
            search_chain=(),
            search_fn=search_fn,
            fetch_fn=fetch_fn,
            depth="fast",
        )
        assert out.sources == ()
        assert "(0 sources)" in out.markdown


class TestPipelinePartialFailure:
    async def test_per_query_search_exception_logs_and_continues(self) -> None:
        async def search_fn(query: str, max_results: int) -> tuple[SearchResult, ...]:
            if query == "q1":
                raise RuntimeError("search exploded")
            return (SearchResult(title="ok", url="https://ok.com", snippet="ok"),)

        # Force two sub-queries via template expansion.
        async def expand_stub(*args, **kwargs) -> tuple[str, ...]:
            return ("q1", "q2")

        from llm_code.tools.research import pipeline as pipeline_mod
        original = pipeline_mod.expand
        pipeline_mod.expand = expand_stub  # type: ignore[assignment]
        try:
            out = await run_research(
                "q",
                profile=_profile(),
                search_chain=(),
                search_fn=search_fn,
                fetch_fn=_make_fetch_fn({
                    "https://ok.com": "body " * 50,
                }),
                # depth="standard" lets both sub-queries run; on fast we'd
                # only see q1 (which raises) and lose q2's success.
                depth="standard",
                max_results=3,
                rerank=IdentityRerankBackend(),
            )
        finally:
            pipeline_mod.expand = original  # type: ignore[assignment]
        assert len(out.sources) == 1
        assert out.sources[0].url == "https://ok.com"

    async def test_per_url_fetch_exception_uses_snippet_fallback(self) -> None:
        results = [
            SearchResult(title="A", url="https://a.com", snippet="A " * 80),
            SearchResult(title="B", url="https://b.com", snippet="B snip"),
        ]
        out = await run_research(
            "q",
            profile=_profile(),
            search_chain=(),
            search_fn=_make_search_fn({"q": results}),
            fetch_fn=_make_fetch_fn(
                {"https://b.com": "B body " * 50},
                fail_urls=("https://a.com",),
            ),
            depth="fast",
            max_results=3,
        )
        # A's fetch failed, but its snippet keeps it ranked; B succeeded.
        urls = {s.url for s in out.sources}
        assert "https://b.com" in urls
        # Pipeline survives despite the per-URL exception.
        assert isinstance(out, ResearchOutput)


class TestRerankSkippedOnFast:
    async def test_fast_depth_uses_search_native_order(self) -> None:
        results = [
            SearchResult(title=f"T{i}", url=f"https://x.com/{i}", snippet=f"s{i}")
            for i in range(5)
        ]
        rerank_mock = MagicMock(spec=IdentityRerankBackend)
        out = await run_research(
            "q",
            profile=_profile(),
            search_chain=(),
            search_fn=_make_search_fn({"q": results}),
            fetch_fn=_make_fetch_fn({
                f"https://x.com/{i}": f"body {i} " * 50 for i in range(5)
            }),
            rerank=rerank_mock,
            depth="fast",
            max_results=2,
        )
        # rerank not called on fast depth.
        rerank_mock.rerank.assert_not_called()
        # Output order matches search order.
        assert [s.url for s in out.sources][:2] == [
            "https://x.com/0", "https://x.com/1",
        ]


class TestLinkupShortCircuit:
    async def test_linkup_short_circuit_fires_when_profile_asks(
        self,
    ) -> None:
        # Build a fake LinkupBackend instance via the real class so the
        # isinstance check inside the pipeline fires.
        backend = LinkupBackend(api_key="test-key")
        sourced = SourcedAnswer(
            answer="A multi-sentence answer with citations.",
            sources=(
                LinkupSource(title="Src 1", url="https://s1.com", snippet="snip 1"),
                LinkupSource(title="Src 2", url="https://s2.com", snippet="snip 2"),
            ),
        )
        # Patch sourced_answer on the instance via attribute injection.
        backend.sourced_answer = MagicMock(return_value=sourced)  # type: ignore[method-assign]

        out = await run_research(
            "factual q",
            profile=_profile(linkup_default_mode="sourcedAnswer"),
            search_chain=(backend,),
            search_fn=_make_search_fn({}),
            fetch_fn=_make_fetch_fn({}),
            depth="standard",
        )
        assert out.backend == "linkup_sourced"
        assert "multi-sentence answer" in out.answer
        assert len(out.sources) == 2

    async def test_linkup_short_circuit_skipped_when_unhealthy(self) -> None:
        # Open the circuit on Linkup before running.
        for _ in range(3):
            _health.record_failure("linkup")

        backend = LinkupBackend(api_key="test-key")
        backend.sourced_answer = MagicMock(  # type: ignore[method-assign]
            side_effect=AssertionError("should not be called"),
        )
        # Pipeline must fall through to the normal path.
        out = await run_research(
            "q",
            profile=_profile(linkup_default_mode="sourcedAnswer"),
            search_chain=(backend,),
            search_fn=_make_search_fn({}),
            fetch_fn=_make_fetch_fn({}),
            depth="fast",
        )
        assert out.backend == "pipeline"

    async def test_linkup_short_circuit_silent_fallback_on_error(self) -> None:
        backend = LinkupBackend(api_key="test-key")
        backend.sourced_answer = MagicMock(  # type: ignore[method-assign]
            side_effect=RuntimeError("api down"),
        )
        out = await run_research(
            "q",
            profile=_profile(linkup_default_mode="sourcedAnswer"),
            search_chain=(backend,),
            search_fn=_make_search_fn({}),
            fetch_fn=_make_fetch_fn({}),
            depth="fast",
        )
        # Falls through to pipeline (which has empty results in this test).
        assert out.backend == "pipeline"

    async def test_linkup_short_circuit_skipped_when_no_linkup_in_chain(self) -> None:
        # No LinkupBackend in the chain → never short-circuits even if
        # profile asks.
        out = await run_research(
            "q",
            profile=_profile(linkup_default_mode="sourcedAnswer"),
            search_chain=(),
            search_fn=_make_search_fn({}),
            fetch_fn=_make_fetch_fn({}),
            depth="fast",
        )
        assert out.backend == "pipeline"


class TestRerankIntegration:
    async def test_rerank_can_reorder_sources(self) -> None:
        # Custom rerank backend that flips the order.
        class FlipRerank:
            @property
            def name(self) -> str:
                return "flip"

            def rerank(self, query, documents, top_k=5):
                return tuple(
                    RerankResult(document=d, score=i, original_index=len(documents) - 1 - i)
                    for i, d in enumerate(reversed(documents))
                )[:top_k]

        results = [
            SearchResult(title="A", url="https://a.com", snippet="A " * 30),
            SearchResult(title="B", url="https://b.com", snippet="B " * 30),
            SearchResult(title="C", url="https://c.com", snippet="C " * 30),
        ]
        out = await run_research(
            "q",
            profile=_profile(),
            search_chain=(),
            search_fn=_make_search_fn({"q": results}),
            fetch_fn=_make_fetch_fn({
                "https://a.com": "body A " * 30,
                "https://b.com": "body B " * 30,
                "https://c.com": "body C " * 30,
            }),
            rerank=FlipRerank(),
            depth="standard",
            max_results=3,
        )
        # Flip rerank reverses order: C, B, A.
        assert [s.url for s in out.sources] == [
            "https://c.com", "https://b.com", "https://a.com",
        ]


class TestFormatMarkdown:
    def test_zero_sources(self) -> None:
        md = _format_markdown("X", ())
        assert "(0 sources)" in md

    def test_includes_url_and_score(self) -> None:
        sources = (
            ResearchSource(title="t", url="https://x.com", snippet="snip", score=0.5),
        )
        md = _format_markdown("q", sources)
        assert "https://x.com" in md
        assert "0.500" in md
        assert "(1 sources)" in md


class TestConcurrencySemaphore:
    async def test_concurrency_capped_by_profile(self) -> None:
        import asyncio
        in_flight = [0]
        max_in_flight = [0]

        async def search_fn(query, max_results):
            in_flight[0] += 1
            max_in_flight[0] = max(max_in_flight[0], in_flight[0])
            await asyncio.sleep(0.01)
            in_flight[0] -= 1
            return (SearchResult(title="t", url=f"https://x.com/{query}", snippet="s"),)

        async def fetch_fn(url):
            return "body " * 60

        # 3 sub-queries, semaphore=2 → max 2 simultaneous.
        async def expand_stub(*args, **kwargs):
            return ("a", "b", "c")

        from llm_code.tools.research import pipeline as pipeline_mod
        original = pipeline_mod.expand
        pipeline_mod.expand = expand_stub  # type: ignore[assignment]
        try:
            await run_research(
                "q",
                profile=_profile(research_max_concurrency=2),
                search_chain=(),
                search_fn=search_fn,
                fetch_fn=fetch_fn,
                depth="fast",
                max_results=3,
            )
        finally:
            pipeline_mod.expand = original  # type: ignore[assignment]
        assert max_in_flight[0] <= 2


class TestRerankBackendResolverFallback:
    async def test_unknown_rerank_backend_falls_back_to_identity(self) -> None:
        # ``rerank_backend = "bogus"`` → factory raises → identity used.
        results = [
            SearchResult(title=f"T{i}", url=f"https://x.com/{i}", snippet="s")
            for i in range(3)
        ]
        out = await run_research(
            "q",
            profile=_profile(rerank_backend="bogus"),
            search_chain=(),
            search_fn=_make_search_fn({"q": results}),
            fetch_fn=_make_fetch_fn({
                f"https://x.com/{i}": "body " * 50 for i in range(3)
            }),
            depth="standard",
            max_results=3,
        )
        # Pipeline still produced sources via identity fallback.
        assert len(out.sources) == 3
