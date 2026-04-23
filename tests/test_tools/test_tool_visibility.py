"""Tests for per-turn tool visibility control."""
from __future__ import annotations

from llm_code.tools.tool_visibility import (
    ALWAYS_VISIBLE,
    INTENT_TOOL_GROUPS,
    classify_intents,
    visible_tools_for_turn,
)

ALL_TOOLS: frozenset[str] = frozenset({
    "read_file", "write_file", "edit_file", "multi_edit",
    "bash", "glob_search", "grep_search", "tool_search",
    "web_search", "web_fetch",
    "git_status", "git_diff", "git_log", "git_commit", "git_push",
    "git_stash", "git_branch",
    "notebook_read", "notebook_edit",
    "lsp_hover", "lsp_goto_definition", "lsp_find_references",
    "lsp_diagnostics", "lsp_document_symbol", "lsp_workspace_symbol",
    "agent", "ask_user", "enter_plan_mode",
    "mcp__slack__post", "mcp__github__pr",
})


class TestClassifyIntents:
    def test_code_edit_english(self) -> None:
        assert "code_edit" in classify_intents("fix the bug in main.py")

    def test_code_edit_chinese(self) -> None:
        assert "code_edit" in classify_intents("幫我修改 README")

    def test_git_intent(self) -> None:
        assert "git" in classify_intents("commit and push to main")

    def test_web_intent(self) -> None:
        assert "web" in classify_intents("search the web for Python docs")

    def test_test_intent(self) -> None:
        assert "test" in classify_intents("run pytest on the project")

    def test_multi_intent(self) -> None:
        intents = classify_intents("改 README 然後 push 到 GitHub")
        assert "code_edit" in intents
        assert "git" in intents

    def test_no_intent(self) -> None:
        assert classify_intents("hello world") == []

    def test_notebook(self) -> None:
        assert "notebook" in classify_intents("edit the jupyter notebook")

    def test_plan(self) -> None:
        assert "plan" in classify_intents("explain how the auth module works")

    def test_chinese_search(self) -> None:
        assert "search" in classify_intents("搜尋 config 在哪裡")


class TestVisibleToolsForTurn:
    def test_always_visible_included(self) -> None:
        result = visible_tools_for_turn(ALL_TOOLS, "hello")
        for tool in ALWAYS_VISIBLE:
            assert tool in result

    def test_mcp_always_included(self) -> None:
        result = visible_tools_for_turn(ALL_TOOLS, "hello")
        assert "mcp__slack__post" in result
        assert "mcp__github__pr" in result

    def test_code_edit_includes_file_tools(self) -> None:
        result = visible_tools_for_turn(ALL_TOOLS, "fix the bug")
        assert "edit_file" in result
        assert "write_file" in result

    def test_git_includes_commit_push(self) -> None:
        result = visible_tools_for_turn(ALL_TOOLS, "commit and push")
        assert "git_commit" in result
        assert "git_push" in result

    def test_multi_intent_union(self) -> None:
        result = visible_tools_for_turn(ALL_TOOLS, "改 README 然後 push")
        assert "edit_file" in result  # from code_edit
        assert "git_push" in result   # from git

    def test_no_intent_defaults_to_code_edit(self) -> None:
        result = visible_tools_for_turn(ALL_TOOLS, "hello")
        code_edit_tools = INTENT_TOOL_GROUPS["code_edit"]
        for tool in code_edit_tools:
            if tool in ALL_TOOLS:
                assert tool in result

    def test_unknown_tools_not_added(self) -> None:
        small = frozenset({"read_file", "bash"})
        result = visible_tools_for_turn(small, "fix bug")
        assert "write_file" not in result  # not in all_tool_names

    def test_max_tools_cap(self) -> None:
        result = visible_tools_for_turn(ALL_TOOLS, "改 README 然後 push 並跑測試 搜尋網路", max_tools=10)
        # Always visible + MCP should still be there
        for tool in ALWAYS_VISIBLE:
            assert tool in result

    def test_bash_always_available(self) -> None:
        """bash is the universal fallback — always visible."""
        result = visible_tools_for_turn(ALL_TOOLS, "some random request")
        assert "bash" in result

    def test_empty_tools(self) -> None:
        result = visible_tools_for_turn(frozenset(), "fix bug")
        assert result == frozenset()

    def test_returns_frozenset(self) -> None:
        result = visible_tools_for_turn(ALL_TOOLS, "fix bug")
        assert isinstance(result, frozenset)


class TestRealTimeIntentAndDispatchTools:
    """Regression coverage for the v2.2.1 → v2.2.2 fix:

    1. ``tool_search`` must survive the intent filter so the deferred-
       tool unlock path is always reachable.
    2. ``agent`` must survive so sub-agent delegation is always
       reachable.
    3. Real-time queries ("today's news", "最新 X", "latest Y") must
       classify as ``web`` intent so ``web_search`` lands in the
       visible set — not just explicit "search the web" phrasing.
    """

    def test_tool_search_always_visible(self) -> None:
        assert "tool_search" in ALWAYS_VISIBLE
        result = visible_tools_for_turn(ALL_TOOLS, "fix bug")
        assert "tool_search" in result

    def test_agent_always_visible(self) -> None:
        assert "agent" in ALWAYS_VISIBLE
        result = visible_tools_for_turn(ALL_TOOLS, "fix bug")
        assert "agent" in result

    def test_today_news_chinese_classifies_web(self) -> None:
        """The exact screenshot query that surfaced the bug."""
        assert "web" in classify_intents("今日熱門新聞三則")

    def test_latest_news_english_classifies_web(self) -> None:
        assert "web" in classify_intents("give me today's top news")
        assert "web" in classify_intents("latest python 3.13 release notes")

    def test_current_events_classifies_web(self) -> None:
        assert "web" in classify_intents("what is currently trending")
        assert "web" in classify_intents("最新 FastAPI 版本是什麼")

    def test_real_time_hyphenated_classifies_web(self) -> None:
        assert "web" in classify_intents("give me real-time stock prices")

    def test_web_search_visible_on_chinese_news_query(self) -> None:
        """End-to-end: the user's screenshot query must now surface
        web_search in the visible tool set."""
        result = visible_tools_for_turn(ALL_TOOLS, "今日熱門新聞三則")
        assert "web_search" in result

    def test_web_search_visible_on_latest_release_query(self) -> None:
        result = visible_tools_for_turn(
            ALL_TOOLS, "what's the latest python release"
        )
        assert "web_search" in result

    def test_unrelated_query_does_not_classify_web(self) -> None:
        """Defensive: don't widen the web intent so far that everyday
        code queries pull web_search in and waste tokens."""
        assert "web" not in classify_intents("fix the bug in main.py")
        assert "web" not in classify_intents("跑測試")
