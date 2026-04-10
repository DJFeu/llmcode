"""Tests for fork cache key derivation, forked message construction, and recursion guard."""
from __future__ import annotations

import copy

import pytest

from llm_code.api.types import Message, MessageRequest, TextBlock
from llm_code.runtime.fork_cache import (
    FORK_BOILERPLATE_TAG,
    FORK_DIRECTIVE_PREFIX,
    FORK_PLACEHOLDER_RESULT,
    build_child_message,
    build_forked_messages,
    build_worktree_notice,
    derive_fork_key,
    is_in_fork_child,
)


# ---------------------------------------------------------------------------
# derive_fork_key (existing, kept for compat)
# ---------------------------------------------------------------------------

class TestDeriveForkKey:
    def test_deterministic(self) -> None:
        assert derive_fork_key("sess-abc", "reviewer") == "sess-abc:fork:reviewer"
        assert derive_fork_key("sess-abc", "reviewer") == derive_fork_key("sess-abc", "reviewer")

    def test_handles_empty(self) -> None:
        assert derive_fork_key("", "reviewer") == "root:fork:reviewer"
        assert derive_fork_key("sess", "") == "sess:fork:anon"


# ---------------------------------------------------------------------------
# MessageRequest cache_key (existing, kept for compat)
# ---------------------------------------------------------------------------

class TestMessageRequestCacheKey:
    def test_default_empty(self) -> None:
        req = MessageRequest(
            model="m", messages=(Message(role="user", content=(TextBlock(text="hi"),)),)
        )
        assert req.cache_key == ""

    def test_set(self) -> None:
        req = MessageRequest(
            model="m",
            messages=(Message(role="user", content=(TextBlock(text="hi"),)),),
            cache_key="parent:fork:reviewer",
        )
        assert req.cache_key == "parent:fork:reviewer"


# ---------------------------------------------------------------------------
# build_child_message
# ---------------------------------------------------------------------------

class TestBuildChildMessage:
    def test_contains_boilerplate_tag(self) -> None:
        msg = build_child_message("Analyze auth module")
        assert f"<{FORK_BOILERPLATE_TAG}>" in msg
        assert f"</{FORK_BOILERPLATE_TAG}>" in msg

    def test_contains_directive(self) -> None:
        msg = build_child_message("Analyze auth module")
        assert f"{FORK_DIRECTIVE_PREFIX}Analyze auth module" in msg

    def test_contains_structured_output_format(self) -> None:
        msg = build_child_message("test")
        assert "Scope:" in msg
        assert "Result:" in msg
        assert "Key files:" in msg

    def test_anti_recursion_rule(self) -> None:
        msg = build_child_message("test")
        assert "Do NOT spawn sub-agents" in msg


# ---------------------------------------------------------------------------
# build_forked_messages — byte-identical prefix verification
# ---------------------------------------------------------------------------

class TestBuildForkedMessages:
    @pytest.fixture
    def parent_assistant_msg(self) -> dict:
        return {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Let me analyze this in parallel."},
                {
                    "type": "tool_use",
                    "id": "toolu_01",
                    "name": "agent",
                    "input": {"task": "A"},
                },
                {
                    "type": "tool_use",
                    "id": "toolu_02",
                    "name": "agent",
                    "input": {"task": "B"},
                },
            ],
        }

    def test_returns_two_messages(self, parent_assistant_msg: dict) -> None:
        result = build_forked_messages("Do task A", parent_assistant_msg)
        assert len(result) == 2
        assert result[0]["role"] == "assistant"
        assert result[1]["role"] == "user"

    def test_byte_identical_prefix_across_children(
        self, parent_assistant_msg: dict,
    ) -> None:
        """The critical invariant: all children produce identical prefixes."""
        msgs_a = build_forked_messages("Analyze auth", parent_assistant_msg)
        msgs_b = build_forked_messages("Analyze payments", parent_assistant_msg)

        # Assistant messages must be identical
        assert msgs_a[0] == msgs_b[0]

        # User message tool_results must be identical
        user_a = msgs_a[1]["content"]
        user_b = msgs_b[1]["content"]

        # All content blocks except the last (directive) must match
        assert user_a[:-1] == user_b[:-1]

        # The last block (directive) must differ
        assert user_a[-1] != user_b[-1]

    def test_placeholder_text_is_constant(
        self, parent_assistant_msg: dict,
    ) -> None:
        result = build_forked_messages("task", parent_assistant_msg)
        user_content = result[1]["content"]
        tool_results = [b for b in user_content if b.get("type") == "tool_result"]
        for tr in tool_results:
            assert tr["content"][0]["text"] == FORK_PLACEHOLDER_RESULT

    def test_tool_use_ids_match_parent(
        self, parent_assistant_msg: dict,
    ) -> None:
        result = build_forked_messages("task", parent_assistant_msg)
        user_content = result[1]["content"]
        tool_results = [b for b in user_content if b.get("type") == "tool_result"]
        assert tool_results[0]["tool_use_id"] == "toolu_01"
        assert tool_results[1]["tool_use_id"] == "toolu_02"

    def test_does_not_mutate_parent(self, parent_assistant_msg: dict) -> None:
        original = copy.deepcopy(parent_assistant_msg)
        build_forked_messages("task", parent_assistant_msg)
        assert parent_assistant_msg == original

    def test_degenerate_no_tool_use(self) -> None:
        msg = {"role": "assistant", "content": [{"type": "text", "text": "hi"}]}
        result = build_forked_messages("directive", msg)
        assert len(result) == 1
        assert result[0]["role"] == "user"

    def test_directive_is_last_block(
        self, parent_assistant_msg: dict,
    ) -> None:
        result = build_forked_messages("my directive", parent_assistant_msg)
        last_block = result[1]["content"][-1]
        assert last_block["type"] == "text"
        assert FORK_DIRECTIVE_PREFIX in last_block["text"]
        assert "my directive" in last_block["text"]

    def test_many_children_all_share_prefix(
        self, parent_assistant_msg: dict,
    ) -> None:
        """Verify N>2 children still share the same prefix."""
        directives = [f"Task {i}" for i in range(5)]
        all_msgs = [build_forked_messages(d, parent_assistant_msg) for d in directives]

        # All assistant messages identical
        for msgs in all_msgs[1:]:
            assert msgs[0] == all_msgs[0][0]

        # All user prefixes (everything except last block) identical
        base_prefix = all_msgs[0][1]["content"][:-1]
        for msgs in all_msgs[1:]:
            assert msgs[1]["content"][:-1] == base_prefix


# ---------------------------------------------------------------------------
# is_in_fork_child — recursion guard
# ---------------------------------------------------------------------------

class TestIsInForkChild:
    def test_detects_boilerplate_in_list_content(self) -> None:
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": f"<{FORK_BOILERPLATE_TAG}>\nfoo"},
                ],
            }
        ]
        assert is_in_fork_child(messages) is True

    def test_detects_boilerplate_in_string_content(self) -> None:
        messages = [
            {"role": "user", "content": f"prefix <{FORK_BOILERPLATE_TAG}> suffix"},
        ]
        assert is_in_fork_child(messages) is True

    def test_false_for_clean_history(self) -> None:
        messages = [
            {"role": "user", "content": [{"type": "text", "text": "hello"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "hi"}]},
        ]
        assert is_in_fork_child(messages) is False

    def test_false_for_empty(self) -> None:
        assert is_in_fork_child([]) is False

    def test_ignores_assistant_messages(self) -> None:
        messages = [
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": f"<{FORK_BOILERPLATE_TAG}>"},
                ],
            }
        ]
        assert is_in_fork_child(messages) is False

    def test_round_trip_with_build_child_message(self) -> None:
        """build_child_message output is detected by is_in_fork_child."""
        msg_text = build_child_message("some directive")
        messages = [
            {"role": "user", "content": [{"type": "text", "text": msg_text}]},
        ]
        assert is_in_fork_child(messages) is True


# ---------------------------------------------------------------------------
# build_worktree_notice
# ---------------------------------------------------------------------------

class TestBuildWorktreeNotice:
    def test_contains_paths(self) -> None:
        notice = build_worktree_notice("/home/user/project", "/tmp/wt-123")
        assert "/home/user/project" in notice
        assert "/tmp/wt-123" in notice

    def test_mentions_isolation(self) -> None:
        notice = build_worktree_notice("/a", "/b")
        assert "isolated" in notice.lower()
        assert "worktree" in notice.lower()


# ---------------------------------------------------------------------------
# orchestrate_executor integration (existing test, migrated)
# ---------------------------------------------------------------------------

class TestOrchestrateExecutorCacheKey:
    def test_passes_cache_key(self) -> None:
        import asyncio
        from types import SimpleNamespace

        from llm_code.api.types import MessageResponse, TokenUsage
        from llm_code.runtime.orchestrate_executor import inline_persona_executor

        captured: dict = {}

        class FakeProvider:
            async def send_message(self, request):
                captured["req"] = request
                return MessageResponse(
                    content=(TextBlock(text="ok"),),
                    usage=TokenUsage(input_tokens=1, output_tokens=1),
                    stop_reason="end",
                )

        runtime = SimpleNamespace(
            _provider=FakeProvider(),
            _config=SimpleNamespace(model="test-model"),
            session=SimpleNamespace(session_id="sess-xyz"),
        )
        persona = SimpleNamespace(
            name="planner", system_prompt="you plan", temperature=0.3
        )
        ok, text = asyncio.run(inline_persona_executor(runtime, persona, "do thing"))
        assert ok is True
        assert captured["req"].cache_key == "sess-xyz:fork:planner"


# ---------------------------------------------------------------------------
# AgentTool parallel fork
# ---------------------------------------------------------------------------

class TestAgentToolParallelFork:
    def test_fork_directives_validation(self) -> None:
        from llm_code.tools.agent import AgentTool

        tool = AgentTool(runtime_factory=lambda m, role=None: None)

        # Both task and fork_directives
        res = tool.execute({"task": "x", "fork_directives": ["a"]})
        assert res.is_error
        assert "not both" in res.output

        # Neither
        res = tool.execute({})
        assert res.is_error

    def test_fork_max_children(self) -> None:
        from llm_code.tools.agent import AgentTool

        tool = AgentTool(runtime_factory=lambda m, role=None: None)
        res = tool.execute({"fork_directives": [f"t{i}" for i in range(11)]})
        assert res.is_error
        assert "max 10" in res.output

    def test_depth_guard_blocks_fork(self) -> None:
        from llm_code.tools.agent import AgentTool

        tool = AgentTool(
            runtime_factory=lambda m, role=None: None,
            max_depth=2,
            current_depth=2,
        )
        res = tool.execute({"fork_directives": ["task"]})
        assert res.is_error
        assert "Max agent depth" in res.output
