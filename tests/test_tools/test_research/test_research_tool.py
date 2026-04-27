"""ResearchTool integration tests (v2.8.0 M5)."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from llm_code.runtime.model_profile import ModelProfile
from llm_code.tools.research.research_tool import ResearchInput, ResearchTool
from llm_code.tools.search_backends import SearchResult


class TestResearchToolMetadata:
    def test_name(self) -> None:
        assert ResearchTool().name == "research"

    def test_is_async_flag(self) -> None:
        assert ResearchTool.is_async is True

    def test_input_model(self) -> None:
        assert ResearchTool().input_model is ResearchInput

    def test_input_schema_required(self) -> None:
        schema = ResearchTool().input_schema
        assert schema["type"] == "object"
        assert "query" in schema["properties"]
        assert "depth" in schema["properties"]
        assert schema["required"] == ["query"]

    def test_input_schema_depth_enum(self) -> None:
        schema = ResearchTool().input_schema
        assert schema["properties"]["depth"]["enum"] == ["fast", "standard", "deep"]

    def test_description_mentions_research_pipeline(self) -> None:
        desc = ResearchTool().description
        assert "research" in desc.lower()
        assert "pipeline" in desc.lower() or "rerank" in desc.lower()

    def test_required_permission_read_only(self) -> None:
        from llm_code.tools.base import PermissionLevel
        assert ResearchTool().required_permission == PermissionLevel.READ_ONLY


class TestResearchToolValidation:
    async def test_invalid_input_returns_error(self) -> None:
        tool = ResearchTool()
        result = await tool.execute_async({})  # missing query
        assert result.is_error

    async def test_unknown_depth_rejected(self) -> None:
        tool = ResearchTool()
        result = await tool.execute_async({"query": "X", "depth": "lightning"})
        assert result.is_error


class TestResearchToolExecution:
    async def test_execute_async_invokes_pipeline(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        tool = ResearchTool()
        # Patch the runtime collaborators so the tool runs without
        # touching the network.
        monkeypatch.setattr(
            tool, "_resolve_profile",
            lambda: ModelProfile(rerank_backend="none", research_query_expansion="off"),
        )
        monkeypatch.setattr(tool, "_build_search_chain", lambda: [])

        async def fake_search(query: str, max_results: int):
            return (SearchResult(title="T", url="https://x.com", snippet="snip"),)

        async def fake_fetch(url: str):
            return "body " * 50

        monkeypatch.setattr(tool, "_make_search_fn", lambda chain: fake_search)
        monkeypatch.setattr(tool, "_make_fetch_fn", lambda: fake_fetch)

        result = await tool.execute_async({"query": "X", "depth": "fast"})
        assert not result.is_error
        assert "https://x.com" in result.output
        assert result.metadata is not None
        assert result.metadata["backend"] == "pipeline"
        assert result.metadata["source_count"] >= 1

    async def test_execute_async_pipeline_failure_returns_error(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        tool = ResearchTool()
        monkeypatch.setattr(tool, "_resolve_profile", lambda: ModelProfile())
        monkeypatch.setattr(tool, "_build_search_chain", lambda: [])

        async def boom_search(*args, **kwargs):
            return ()

        async def boom_fetch(*args, **kwargs):
            return ""

        monkeypatch.setattr(tool, "_make_search_fn", lambda chain: boom_search)
        monkeypatch.setattr(tool, "_make_fetch_fn", lambda: boom_fetch)

        # Patch run_research at the pipeline-module attribute it's
        # imported from. The tool does ``from llm_code.tools.research.pipeline
        # import run_research`` inside execute_async, so patching the
        # module attribute is sufficient.
        from llm_code.tools.research import pipeline as pipeline_mod
        original = pipeline_mod.run_research

        async def boom_run(*args, **kwargs):
            raise RuntimeError("simulated failure")

        pipeline_mod.run_research = boom_run  # type: ignore[assignment]
        try:
            result = await tool.execute_async({"query": "X"})
        finally:
            pipeline_mod.run_research = original  # type: ignore[assignment]
        assert result.is_error
        assert "simulated failure" in result.output


class TestResearchToolHelpers:
    def test_resolve_profile_falls_back_on_exception(self) -> None:
        tool = ResearchTool()
        with patch(
            "llm_code.runtime.config.RuntimeConfig",
            side_effect=Exception("boom"),
        ):
            profile = tool._resolve_profile()
        assert isinstance(profile, ModelProfile)

    def test_build_search_chain_includes_duckduckgo(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Strip env vars so only DDG (no key) is built.
        for var in ("BRAVE_API_KEY", "EXA_API_KEY", "JINA_API_KEY",
                    "LINKUP_API_KEY", "TAVILY_API_KEY", "SERPER_API_KEY"):
            monkeypatch.delenv(var, raising=False)
        tool = ResearchTool()
        chain = tool._build_search_chain()
        names = [b.name for b in chain]
        assert "duckduckgo" in names

    async def test_make_search_fn_returns_first_non_empty(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        tool = ResearchTool()

        class Backend:
            def __init__(self, name: str, results: tuple) -> None:
                self._name = name
                self._results = results

            @property
            def name(self) -> str:
                return self._name

            def search(self, query: str, *, max_results: int = 10) -> tuple:
                return self._results

        b1 = Backend("first", ())
        b2 = Backend("second", (
            SearchResult(title="t", url="https://x.com", snippet="s"),
        ))
        search_fn = tool._make_search_fn([b1, b2])
        results = await search_fn("q", 5)
        assert len(results) == 1
        assert results[0].url == "https://x.com"

    async def test_make_search_fn_handles_exception(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from llm_code.tools.search_backends import RateLimitError

        tool = ResearchTool()

        class FailBackend:
            @property
            def name(self) -> str:
                return "fail"

            def search(self, query, *, max_results=10):
                raise RateLimitError("429")

        class GoodBackend:
            @property
            def name(self) -> str:
                return "good"

            def search(self, query, *, max_results=10):
                return (SearchResult(title="ok", url="https://ok.com", snippet="s"),)

        search_fn = tool._make_search_fn([FailBackend(), GoodBackend()])
        results = await search_fn("q", 5)
        assert len(results) == 1
        assert results[0].url == "https://ok.com"

    async def test_make_fetch_fn_returns_empty_on_jina_failure(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from llm_code.tools.web_fetch import JinaReaderError

        async def fake_jina_fetch(url, **kwargs):
            raise JinaReaderError("simulated")

        monkeypatch.setattr(
            "llm_code.tools.web_fetch.fetch_via_jina_reader_async",
            fake_jina_fetch,
        )
        tool = ResearchTool()
        fetch_fn = tool._make_fetch_fn()
        body = await fetch_fn("https://x.com")
        assert body == ""

    async def test_make_fetch_fn_returns_body_on_success(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def fake_jina_fetch(url, **kwargs):
            return ("# hello world", "text/markdown", 200)

        monkeypatch.setattr(
            "llm_code.tools.web_fetch.fetch_via_jina_reader_async",
            fake_jina_fetch,
        )
        tool = ResearchTool()
        fetch_fn = tool._make_fetch_fn()
        body = await fetch_fn("https://x.com")
        assert body == "# hello world"


class TestResearchToolBuiltinRegistration:
    def test_research_in_builtin_tools(self) -> None:
        from llm_code.tools.builtin import get_builtin_tools
        tools = get_builtin_tools()
        assert "research" in tools

    def test_research_tool_class_registered(self) -> None:
        from llm_code.tools.builtin import get_builtin_tools
        tools = get_builtin_tools()
        assert tools["research"] is ResearchTool
