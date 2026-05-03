from __future__ import annotations

import pytest

from llm_code.tools.base import PermissionLevel, Tool, ToolResult
from llm_code.tools.registry import ToolRegistry


class _FakeWebSearch(Tool):
    def __init__(self) -> None:
        self.calls: list[dict] = []

    @property
    def name(self) -> str:
        return "web_search"

    @property
    def description(self) -> str:
        return "fake search"

    @property
    def input_schema(self) -> dict:
        return {"type": "object"}

    @property
    def required_permission(self) -> PermissionLevel:
        return PermissionLevel.READ_ONLY

    def execute(self, args: dict) -> ToolResult:
        self.calls.append(args)
        return ToolResult(
            output=(
                '## Search Results for "query"\n\n'
                "1. **[Grounded source](https://news.example/a)**\n"
                "   Fresh snippet\n"
            ),
        )


class _FakeWebFetch(Tool):
    def __init__(self) -> None:
        self.calls: list[dict] = []

    @property
    def name(self) -> str:
        return "web_fetch"

    @property
    def description(self) -> str:
        return "fake fetch"

    @property
    def input_schema(self) -> dict:
        return {"type": "object"}

    @property
    def required_permission(self) -> PermissionLevel:
        return PermissionLevel.READ_ONLY

    def execute(self, args: dict) -> ToolResult:
        self.calls.append(args)
        return ToolResult(
            output=(
                "# Grounded source article\n\n"
                "Concrete current-event detail from fetched page."
            ),
        )


class _FakeLowQualityWebFetch(_FakeWebFetch):
    def execute(self, args: dict) -> ToolResult:
        self.calls.append(args)
        return ToolResult(
            output=(
                "window.WIZ_global_data = {}; "
                "var googletag = googletag || {}; "
                "function init(){ document.createElement('script'); }"
            ),
        )


class _FakeWeakHomepageSearch(Tool):
    @property
    def name(self) -> str:
        return "web_search"

    @property
    def description(self) -> str:
        return "fake weak search"

    @property
    def input_schema(self) -> dict:
        return {"type": "object"}

    @property
    def required_permission(self) -> PermissionLevel:
        return PermissionLevel.READ_ONLY

    def execute(self, args: dict) -> ToolResult:
        return ToolResult(
            output=(
                '## Search Results for "query"\n\n'
                "1. **[Generic News Homepage](https://news.example/)**\n"
                "   Provides the latest news from many sources.\n\n"
                "2. **[Concrete article list](https://news.example/list/popular)**\n"
                "   Mayor announces concrete transport policy update.\n\n"
                "(2 results)"
            ),
        )


@pytest.mark.parametrize(
    "prompt",
    [
        "顯示今日熱門新聞三則",
        "查詢 Qwen 3.6 最新 release note",
        "latest llama.cpp server options",
        "搜尋 2026 台灣 AI 法規更新",
    ],
)
def test_web_rag_detects_external_knowledge_prompts(prompt: str) -> None:
    from llm_code.runtime.web_rag import should_augment_with_web

    assert should_augment_with_web(prompt) is True


@pytest.mark.parametrize(
    "prompt",
    [
        "解這題 grid 雙車移動，要求 O(mn)",
        "請重構這個 Python function",
        "說明 DFS 和 BFS 的差異",
    ],
)
def test_web_rag_does_not_trigger_for_pure_coding_or_static_prompts(prompt: str) -> None:
    from llm_code.runtime.web_rag import should_augment_with_web

    assert should_augment_with_web(prompt) is False


@pytest.mark.asyncio
async def test_web_rag_context_runs_web_search_for_detected_prompt() -> None:
    from llm_code.runtime.web_rag import build_web_rag_context

    registry = ToolRegistry()
    search = _FakeWebSearch()
    registry.register(search)

    context = await build_web_rag_context("顯示今日熱門新聞三則", registry)

    assert search.calls
    assert search.calls[0]["query"] == "顯示今日熱門新聞三則"
    assert search.calls[0]["max_results"] == 10
    assert "Web RAG context" in context
    assert "Grounded source" in context
    assert "Do not invent" in context


@pytest.mark.asyncio
async def test_web_rag_filters_weak_homepage_search_results() -> None:
    from llm_code.runtime.web_rag import build_web_rag_context

    registry = ToolRegistry()
    registry.register(_FakeWeakHomepageSearch())

    context = await build_web_rag_context("顯示今日熱門新聞三則", registry)

    assert "Generic News Homepage" not in context
    assert "Provides the latest news from many sources" not in context
    assert "Concrete article list" in context
    assert "Mayor announces concrete transport policy update" in context


@pytest.mark.asyncio
async def test_web_rag_context_fetches_top_search_results_when_available() -> None:
    from llm_code.runtime.web_rag import build_web_rag_context

    registry = ToolRegistry()
    search = _FakeWebSearch()
    fetch = _FakeWebFetch()
    registry.register(search)
    registry.register(fetch)

    context = await build_web_rag_context("顯示今日熱門新聞三則", registry)

    assert fetch.calls
    assert fetch.calls[0]["url"] == "https://news.example/a"
    assert fetch.calls[0]["max_length"] == 6000
    assert "Fetched source excerpts" in context
    assert "Concrete current-event detail" in context
    assert "Prefer fetched source excerpts" in context
    assert "cite the source title or URL" in context


@pytest.mark.asyncio
async def test_web_rag_context_drops_low_quality_fetched_javascript() -> None:
    from llm_code.runtime.web_rag import build_web_rag_context

    registry = ToolRegistry()
    registry.register(_FakeWebSearch())
    registry.register(_FakeLowQualityWebFetch())

    context = await build_web_rag_context("顯示今日熱門新聞三則", registry)

    assert "window.WIZ_global_data" not in context
    assert "document.createElement" not in context


@pytest.mark.asyncio
async def test_web_rag_context_skips_when_web_search_missing() -> None:
    from llm_code.runtime.web_rag import build_web_rag_context

    context = await build_web_rag_context("latest AI news", ToolRegistry())

    assert context == ""


@pytest.mark.asyncio
async def test_runtime_injects_web_rag_context_into_system_prompt(tmp_path) -> None:
    from llm_code.api.types import MessageRequest, StreamMessageStop, StreamTextDelta, TokenUsage
    from llm_code.runtime.config import RuntimeConfig
    from llm_code.runtime.context import ProjectContext
    from llm_code.runtime.conversation import ConversationRuntime
    from llm_code.runtime.permissions import PermissionMode, PermissionPolicy
    from llm_code.runtime.prompt import SystemPromptBuilder
    from llm_code.runtime.session import Session

    class _Provider:
        def __init__(self) -> None:
            self.requests: list[MessageRequest] = []

        def supports_native_tools(self) -> bool:
            return False

        def supports_reasoning(self) -> bool:
            return False

        async def stream_message(self, request: MessageRequest):
            self.requests.append(request)

            async def _gen():
                yield StreamTextDelta(text="ok")
                yield StreamMessageStop(
                    usage=TokenUsage(input_tokens=1, output_tokens=1),
                    stop_reason="end_turn",
                )

            return _gen()

    registry = ToolRegistry()
    registry.register(_FakeWebSearch())
    provider = _Provider()
    runtime = ConversationRuntime(
        provider=provider,
        tool_registry=registry,
        permission_policy=PermissionPolicy(mode=PermissionMode.AUTO_ACCEPT),
        hook_runner=None,
        prompt_builder=SystemPromptBuilder(),
        config=RuntimeConfig(model="local-test", max_turn_iterations=1),
        session=Session.create(project_path=tmp_path),
        context=ProjectContext(
            cwd=tmp_path,
            is_git_repo=False,
            git_status="",
            instructions="",
        ),
    )

    async for _ in runtime.run_turn("查詢 Qwen 3.6 最新 release note"):
        pass

    assert provider.requests
    assert "Web RAG context" in provider.requests[0].system
    assert "Grounded source" in provider.requests[0].system


@pytest.mark.asyncio
async def test_runtime_hides_web_tools_after_web_rag_preflight(tmp_path) -> None:
    from llm_code.api.types import MessageRequest, StreamMessageStop, StreamTextDelta, TokenUsage
    from llm_code.runtime.config import RuntimeConfig
    from llm_code.runtime.context import ProjectContext
    from llm_code.runtime.conversation import ConversationRuntime
    from llm_code.runtime.permissions import PermissionMode, PermissionPolicy
    from llm_code.runtime.prompt import SystemPromptBuilder
    from llm_code.runtime.session import Session

    class _Provider:
        def __init__(self) -> None:
            self.requests: list[MessageRequest] = []

        def supports_native_tools(self) -> bool:
            return True

        def supports_reasoning(self) -> bool:
            return False

        async def stream_message(self, request: MessageRequest):
            self.requests.append(request)

            async def _gen():
                yield StreamTextDelta(text="ok")
                yield StreamMessageStop(
                    usage=TokenUsage(input_tokens=1, output_tokens=1),
                    stop_reason="end_turn",
                )

            return _gen()

    registry = ToolRegistry()
    registry.register(_FakeWebSearch())
    registry.register(_FakeWebFetch())
    provider = _Provider()
    runtime = ConversationRuntime(
        provider=provider,
        tool_registry=registry,
        permission_policy=PermissionPolicy(mode=PermissionMode.AUTO_ACCEPT),
        hook_runner=None,
        prompt_builder=SystemPromptBuilder(),
        config=RuntimeConfig(model="local-test", max_turn_iterations=1),
        session=Session.create(project_path=tmp_path),
        context=ProjectContext(
            cwd=tmp_path,
            is_git_repo=False,
            git_status="",
            instructions="",
        ),
    )

    async for _ in runtime.run_turn("顯示今日熱門新聞三則"):
        pass

    assert provider.requests
    assert "Web RAG context" in provider.requests[0].system
    assert {tool.name for tool in provider.requests[0].tools}.isdisjoint({
        "web_search",
        "web_fetch",
    })
