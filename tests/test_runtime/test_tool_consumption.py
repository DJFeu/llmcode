"""Tests for v14 Mechanism A — post-tool reminder injection.

Covers:
  * ``build_post_tool_reminder`` helper — defaults, opt-out, defensive
    empty input, log emission, message shape.
  * Conversation runtime wiring — assert reminder messages are
    appended to the session immediately after tool result messages
    when the active profile opts in, and skipped when it opts out.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import AsyncIterator

import pytest

from llm_code.api.types import (
    Message,
    MessageRequest,
    StreamEvent,
    StreamMessageStop,
    StreamTextDelta,
    StreamToolUseInputDelta,
    StreamToolUseStart,
    TextBlock,
    TokenUsage,
    ToolResultBlock,
)
from llm_code.runtime.context import ProjectContext
from llm_code.runtime.conversation import ConversationRuntime
from llm_code.runtime.model_profile import ModelProfile
from llm_code.runtime.permissions import PermissionMode, PermissionPolicy
from llm_code.runtime.prompt import SystemPromptBuilder
from llm_code.runtime.session import Session
from llm_code.runtime.tool_consumption import (
    _REMINDER_TEMPLATE,
    build_post_tool_reminder,
)
from llm_code.tools.base import PermissionLevel, Tool, ToolResult
from llm_code.tools.registry import ToolRegistry


# =============================================================================
# build_post_tool_reminder unit tests
# =============================================================================


class TestBuildPostToolReminderHelper:
    def test_returns_none_when_flag_false(self) -> None:
        profile = ModelProfile(reminder_after_each_call=False)
        assert build_post_tool_reminder("read_file", profile) is None

    def test_returns_none_when_tool_name_empty(self) -> None:
        profile = ModelProfile(reminder_after_each_call=True)
        assert build_post_tool_reminder("", profile) is None

    def test_returns_none_when_tool_name_none_like(self) -> None:
        """Defensive: any falsy tool_name yields no reminder.

        Catches both ``""`` and the (unlikely) case where a caller
        passes ``None``.
        """
        profile = ModelProfile(reminder_after_each_call=True)
        # Mypy would catch a None pass but the runtime guard exists
        # for callers that bypass the type system (e.g. *args plumbing).
        assert build_post_tool_reminder(None, profile) is None  # type: ignore[arg-type]

    def test_returns_message_when_flag_true(self) -> None:
        profile = ModelProfile(reminder_after_each_call=True)
        result = build_post_tool_reminder("web_search", profile)
        assert result is not None
        assert isinstance(result, Message)

    def test_message_role_is_user(self) -> None:
        """OpenAI-compat protocol's closest analogue to a mid-turn
        system reminder is a ``user`` role message — the reminder
        rides in this role to stay protocol-portable."""
        profile = ModelProfile(reminder_after_each_call=True)
        result = build_post_tool_reminder("read_file", profile)
        assert result is not None
        assert result.role == "user"

    def test_message_content_is_single_textblock(self) -> None:
        profile = ModelProfile(reminder_after_each_call=True)
        result = build_post_tool_reminder("bash", profile)
        assert result is not None
        assert len(result.content) == 1
        assert isinstance(result.content[0], TextBlock)

    def test_reminder_text_contains_tool_name_verbatim(self) -> None:
        profile = ModelProfile(reminder_after_each_call=True)
        result = build_post_tool_reminder("custom_tool_X", profile)
        assert result is not None
        block = result.content[0]
        assert isinstance(block, TextBlock)
        assert "custom_tool_X" in block.text

    def test_reminder_text_contains_system_reminder_block(self) -> None:
        profile = ModelProfile(reminder_after_each_call=True)
        result = build_post_tool_reminder("grep", profile)
        assert result is not None
        block = result.content[0]
        assert isinstance(block, TextBlock)
        assert block.text.startswith("<system-reminder>")
        assert block.text.endswith("</system-reminder>")

    def test_reminder_text_includes_ground_truth_phrase(self) -> None:
        """Spec §3.2 requires the reminder to frame tool output as
        ground truth — the model's RLHF-trained denial habit needs
        an explicit override phrase to bypass."""
        profile = ModelProfile(reminder_after_each_call=True)
        result = build_post_tool_reminder("read_file", profile)
        assert result is not None
        block = result.content[0]
        assert isinstance(block, TextBlock)
        assert "ground truth" in block.text

    def test_reminder_text_includes_do_not_deny_phrase(self) -> None:
        profile = ModelProfile(reminder_after_each_call=True)
        result = build_post_tool_reminder("read_file", profile)
        assert result is not None
        block = result.content[0]
        assert isinstance(block, TextBlock)
        assert "Do NOT deny" in block.text

    def test_reminder_text_handles_empty_or_error_gracefully(self) -> None:
        """The reminder must give the model a legitimate fall-through
        for empty / error tool results so it doesn't reach for a
        denial when the result is simply unhelpful."""
        profile = ModelProfile(reminder_after_each_call=True)
        result = build_post_tool_reminder("web_search", profile)
        assert result is not None
        block = result.content[0]
        assert isinstance(block, TextBlock)
        assert "empty" in block.text or "error" in block.text

    def test_logs_info_event_when_reminder_produced(
        self, caplog: pytest.LogCaptureFixture,
    ) -> None:
        profile = ModelProfile(reminder_after_each_call=True)
        with caplog.at_level(
            logging.INFO, logger="llm_code.runtime.tool_consumption",
        ):
            build_post_tool_reminder("read_file", profile)
        assert any(
            "tool_consumption: reminder_injected" in r.message
            and "tool=read_file" in r.message
            for r in caplog.records
        )

    def test_no_log_when_flag_off(
        self, caplog: pytest.LogCaptureFixture,
    ) -> None:
        profile = ModelProfile(reminder_after_each_call=False)
        with caplog.at_level(
            logging.INFO, logger="llm_code.runtime.tool_consumption",
        ):
            build_post_tool_reminder("read_file", profile)
        assert not any(
            "tool_consumption: reminder_injected" in r.message
            for r in caplog.records
        )

    def test_no_log_when_tool_name_empty(
        self, caplog: pytest.LogCaptureFixture,
    ) -> None:
        profile = ModelProfile(reminder_after_each_call=True)
        with caplog.at_level(
            logging.INFO, logger="llm_code.runtime.tool_consumption",
        ):
            build_post_tool_reminder("", profile)
        assert not any(
            "tool_consumption: reminder_injected" in r.message
            for r in caplog.records
        )

    def test_default_profile_opts_in(self) -> None:
        """Spec §4 — the dataclass default ships with
        ``reminder_after_each_call=True`` so every model gets the
        protection unless the profile explicitly opts out."""
        profile = ModelProfile()
        assert profile.reminder_after_each_call is True
        # And the helper produces a Message with the default profile.
        assert build_post_tool_reminder("read_file", profile) is not None

    def test_log_records_byte_count(
        self, caplog: pytest.LogCaptureFixture,
    ) -> None:
        profile = ModelProfile(reminder_after_each_call=True)
        with caplog.at_level(
            logging.INFO, logger="llm_code.runtime.tool_consumption",
        ):
            build_post_tool_reminder("read_file", profile)
        bytes_lines = [
            r.message for r in caplog.records
            if "tool_consumption: reminder_injected" in r.message
        ]
        assert bytes_lines
        # Format: "...tool=NAME bytes=NN" — assert NN is positive int.
        line = bytes_lines[0]
        token = line.split("bytes=", 1)[1]
        assert int(token) > 0

    def test_template_is_module_constant(self) -> None:
        """Sanity check — the template lives at module level so a
        future plan can override it for tool-category specialisation
        without re-implementing the helper."""
        assert "<system-reminder>" in _REMINDER_TEMPLATE
        assert "{tool_name}" in _REMINDER_TEMPLATE


# =============================================================================
# Conversation runtime wiring tests
# =============================================================================


def _make_context(tmp_path: Path) -> ProjectContext:
    return ProjectContext(
        cwd=tmp_path,
        is_git_repo=False,
        git_status="",
        instructions="",
    )


async def _simple_text_stream() -> AsyncIterator[StreamEvent]:
    yield StreamTextDelta(text="ok")
    yield StreamMessageStop(usage=TokenUsage(5, 5), stop_reason="end_turn")


async def _single_tool_stream() -> AsyncIterator[StreamEvent]:
    yield StreamToolUseStart(id="call1", name="read_file")
    yield StreamToolUseInputDelta(id="call1", partial_json='{"path":"/tmp/f"}')
    yield StreamMessageStop(usage=TokenUsage(20, 10), stop_reason="tool_use")


async def _multi_tool_stream() -> AsyncIterator[StreamEvent]:
    yield StreamToolUseStart(id="callA", name="read_file")
    yield StreamToolUseInputDelta(id="callA", partial_json='{"path":"/tmp/a"}')
    yield StreamToolUseStart(id="callB", name="grep_file")
    yield StreamToolUseInputDelta(id="callB", partial_json='{"path":"/tmp/b"}')
    yield StreamMessageStop(usage=TokenUsage(20, 10), stop_reason="tool_use")


async def _final_text_stream() -> AsyncIterator[StreamEvent]:
    yield StreamTextDelta(text="Done")
    yield StreamMessageStop(usage=TokenUsage(5, 3), stop_reason="end_turn")


class _MockProvider:
    def __init__(self, response_streams: list) -> None:
        self._streams = iter(response_streams)
        self._call_count = 0

    async def stream_message(
        self, request: MessageRequest,
    ) -> AsyncIterator[StreamEvent]:
        self._call_count += 1
        return next(self._streams)

    def supports_native_tools(self) -> bool:
        return True

    def supports_images(self) -> bool:
        return False

    def supports_reasoning(self) -> bool:
        return False


def _make_runtime_with_profile(
    tmp_path: Path,
    provider: _MockProvider,
    registry: ToolRegistry,
    *,
    reminder_after_each_call: bool,
) -> ConversationRuntime:
    permission_policy = PermissionPolicy(mode=PermissionMode.AUTO_ACCEPT)

    class _NoOpHooks:
        async def pre_tool_use(self, tool_name: str, args: dict) -> dict:
            return args

        async def post_tool_use(
            self, tool_name: str, args: dict, result,
        ) -> None:
            pass

    class _Config:
        max_turn_iterations = 5
        max_tokens = 4096
        temperature = 0.7
        native_tools = True
        compact_after_tokens = 80000

    session = Session.create(tmp_path)
    context = _make_context(tmp_path)
    runtime = ConversationRuntime(
        provider=provider,
        tool_registry=registry,
        permission_policy=permission_policy,
        hook_runner=_NoOpHooks(),
        prompt_builder=SystemPromptBuilder(),
        config=_Config(),
        session=session,
        context=context,
    )
    # Overwrite the resolved profile with one whose flag matches the
    # test scenario. The runtime would otherwise pick a profile based
    # on the empty model id (default profile) which already has the
    # flag True; explicit assignment makes the off-case testable.
    runtime._model_profile = ModelProfile(
        reminder_after_each_call=reminder_after_each_call,
    )
    return runtime


class _FakeReadTool(Tool):
    @property
    def name(self) -> str:
        return "read_file"

    @property
    def description(self) -> str:
        return "Read a file"

    @property
    def input_schema(self) -> dict:
        return {"type": "object", "properties": {"path": {"type": "string"}}}

    @property
    def required_permission(self) -> PermissionLevel:
        return PermissionLevel.READ_ONLY

    def execute(self, args: dict) -> ToolResult:
        return ToolResult(output="contents of " + args.get("path", ""))


class _FakeGrepTool(Tool):
    @property
    def name(self) -> str:
        return "grep_file"

    @property
    def description(self) -> str:
        return "Grep a file"

    @property
    def input_schema(self) -> dict:
        return {"type": "object", "properties": {"path": {"type": "string"}}}

    @property
    def required_permission(self) -> PermissionLevel:
        return PermissionLevel.READ_ONLY

    def execute(self, args: dict) -> ToolResult:
        return ToolResult(output="grep results for " + args.get("path", ""))


def _make_registry(*tools: Tool) -> ToolRegistry:
    registry = ToolRegistry()
    for t in tools:
        registry.register(t)
    return registry


class TestConversationWiringSingleTool:
    @pytest.mark.asyncio
    async def test_reminder_appended_after_tool_result_when_flag_on(
        self, tmp_path: Path,
    ) -> None:
        registry = _make_registry(_FakeReadTool())
        provider = _MockProvider([_single_tool_stream(), _final_text_stream()])
        runtime = _make_runtime_with_profile(
            tmp_path, provider, registry, reminder_after_each_call=True,
        )
        async for _ in runtime.run_turn("read it"):
            pass

        # Walk the session message history and find the index of the
        # tool-result message; the next message must be the reminder
        # (role=user, content[0]=TextBlock with <system-reminder>).
        msgs = runtime.session.messages
        tool_result_idx = next(
            i for i, m in enumerate(msgs)
            if any(isinstance(b, ToolResultBlock) for b in m.content)
        )
        reminder_msg = msgs[tool_result_idx + 1]
        assert reminder_msg.role == "user"
        text_blocks = [b for b in reminder_msg.content if isinstance(b, TextBlock)]
        assert text_blocks
        assert "<system-reminder>" in text_blocks[0].text
        assert "read_file" in text_blocks[0].text

    @pytest.mark.asyncio
    async def test_no_reminder_when_flag_off(self, tmp_path: Path) -> None:
        registry = _make_registry(_FakeReadTool())
        provider = _MockProvider([_single_tool_stream(), _final_text_stream()])
        runtime = _make_runtime_with_profile(
            tmp_path, provider, registry, reminder_after_each_call=False,
        )
        async for _ in runtime.run_turn("read it"):
            pass

        # No reminder message in history — every TextBlock that
        # follows a tool-result message should NOT contain the
        # ``<system-reminder>`` marker.
        msgs = runtime.session.messages
        for msg in msgs:
            for block in msg.content:
                if isinstance(block, TextBlock):
                    assert "<system-reminder>" not in block.text

    @pytest.mark.asyncio
    async def test_session_message_count_grows_by_extra_one_when_flag_on(
        self, tmp_path: Path,
    ) -> None:
        registry = _make_registry(_FakeReadTool())
        # Same provider stream, two runtimes — one with flag on, one off.
        provider_on = _MockProvider(
            [_single_tool_stream(), _final_text_stream()],
        )
        runtime_on = _make_runtime_with_profile(
            tmp_path, provider_on, registry, reminder_after_each_call=True,
        )
        async for _ in runtime_on.run_turn("read it"):
            pass

        provider_off = _MockProvider(
            [_single_tool_stream(), _final_text_stream()],
        )
        runtime_off = _make_runtime_with_profile(
            tmp_path / "off", provider_off,
            _make_registry(_FakeReadTool()),
            reminder_after_each_call=False,
        )
        # Session.create needs the dir to exist for off case.
        (tmp_path / "off").mkdir(exist_ok=True)
        # Re-create runtime so session sits under the new dir.
        runtime_off = _make_runtime_with_profile(
            tmp_path / "off", provider_off,
            _make_registry(_FakeReadTool()),
            reminder_after_each_call=False,
        )
        async for _ in runtime_off.run_turn("read it"):
            pass

        diff = (
            len(runtime_on.session.messages)
            - len(runtime_off.session.messages)
        )
        # Exactly one more message in the on case — the reminder.
        assert diff == 1


class TestConversationWiringMultiTool:
    @pytest.mark.asyncio
    async def test_one_reminder_per_tool_call_when_flag_on(
        self, tmp_path: Path,
    ) -> None:
        registry = _make_registry(_FakeReadTool(), _FakeGrepTool())
        provider = _MockProvider([_multi_tool_stream(), _final_text_stream()])
        runtime = _make_runtime_with_profile(
            tmp_path, provider, registry, reminder_after_each_call=True,
        )
        async for _ in runtime.run_turn("read and grep"):
            pass

        msgs = runtime.session.messages
        # Count reminder messages (user role + TextBlock with <system-reminder>).
        reminder_count = 0
        reminder_tools_seen: list[str] = []
        for msg in msgs:
            if msg.role != "user":
                continue
            for block in msg.content:
                if isinstance(block, TextBlock) and "<system-reminder>" in block.text:
                    reminder_count += 1
                    if "read_file" in block.text:
                        reminder_tools_seen.append("read_file")
                    if "grep_file" in block.text:
                        reminder_tools_seen.append("grep_file")
        assert reminder_count == 2
        assert sorted(reminder_tools_seen) == ["grep_file", "read_file"]

    @pytest.mark.asyncio
    async def test_no_reminders_when_flag_off_multi_tool(
        self, tmp_path: Path,
    ) -> None:
        registry = _make_registry(_FakeReadTool(), _FakeGrepTool())
        provider = _MockProvider([_multi_tool_stream(), _final_text_stream()])
        runtime = _make_runtime_with_profile(
            tmp_path, provider, registry, reminder_after_each_call=False,
        )
        async for _ in runtime.run_turn("read and grep"):
            pass

        msgs = runtime.session.messages
        for msg in msgs:
            for block in msg.content:
                if isinstance(block, TextBlock):
                    assert "<system-reminder>" not in block.text

    @pytest.mark.asyncio
    async def test_reminders_follow_tool_result_message(
        self, tmp_path: Path,
    ) -> None:
        """Spec §3.2 — reminders must follow the tool-result message
        in the outbound history so they ride in the same provider call
        as the tool result."""
        registry = _make_registry(_FakeReadTool(), _FakeGrepTool())
        provider = _MockProvider([_multi_tool_stream(), _final_text_stream()])
        runtime = _make_runtime_with_profile(
            tmp_path, provider, registry, reminder_after_each_call=True,
        )
        async for _ in runtime.run_turn("read and grep"):
            pass

        msgs = runtime.session.messages
        # Locate the tool-result message (single bundled Message holding
        # ToolResultBlock entries — current architecture).
        tool_result_idx = next(
            i for i, m in enumerate(msgs)
            if any(isinstance(b, ToolResultBlock) for b in m.content)
        )
        # The two messages immediately after must both be reminders.
        for offset in (1, 2):
            after = msgs[tool_result_idx + offset]
            assert after.role == "user"
            assert any(
                isinstance(b, TextBlock)
                and "<system-reminder>" in b.text
                for b in after.content
            )
