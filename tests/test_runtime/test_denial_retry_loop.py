"""Tests for v14 Mechanism C — denial-retry loop integration.

Drives :class:`ConversationRuntime` end-to-end through a mocked
provider that returns scripted streams. Verifies the retry loop
fires when the active profile opts in via ``retry_on_denial=True``,
the 1-retry cap holds even on persistent denials, and the loop
stays dormant for profiles with the flag off.

Buffered streaming UX is exercised: when retry fires, the original
denial deltas must NOT reach the caller; when retry succeeds (or
the flag is off), the substantive content must reach the caller.
"""
from __future__ import annotations

from pathlib import Path
from typing import AsyncIterator

import pytest

from llm_code.api.types import (
    MessageRequest,
    StreamEvent,
    StreamMessageStop,
    StreamTextDelta,
    StreamToolUseInputDelta,
    StreamToolUseStart,
    TextBlock,
    TokenUsage,
)
from llm_code.runtime.context import ProjectContext
from llm_code.runtime.conversation import ConversationRuntime
from llm_code.runtime.model_profile import ModelProfile
from llm_code.runtime.permissions import PermissionMode, PermissionPolicy
from llm_code.runtime.prompt import SystemPromptBuilder
from llm_code.runtime.session import Session
from llm_code.tools.base import PermissionLevel, Tool, ToolResult
from llm_code.tools.registry import ToolRegistry


# =============================================================================
# Helpers
# =============================================================================


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


def _make_context(tmp_path: Path) -> ProjectContext:
    return ProjectContext(
        cwd=tmp_path,
        is_git_repo=False,
        git_status="",
        instructions="",
    )


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


async def _tool_call_stream(call_id: str = "call1") -> AsyncIterator[StreamEvent]:
    yield StreamToolUseStart(id=call_id, name="read_file")
    yield StreamToolUseInputDelta(
        id=call_id, partial_json='{"path":"/tmp/f"}',
    )
    yield StreamMessageStop(usage=TokenUsage(20, 10), stop_reason="tool_use")


async def _denial_stream() -> AsyncIterator[StreamEvent]:
    """First-call response: a textbook denial after a tool succeeded.

    GLM-5.1's canonical failure mode in production. Detector should
    fire on this and trigger a retry."""
    yield StreamTextDelta(
        text="I don't have access to news APIs or real-time data. "
        "I'm just a coding assistant.",
    )
    yield StreamMessageStop(usage=TokenUsage(15, 30), stop_reason="end_turn")


async def _substantive_stream() -> AsyncIterator[StreamEvent]:
    """Retry response: the model takes the reminder and consumes the
    tool result properly."""
    yield StreamTextDelta(
        text="Based on the file contents above, the user's profile "
        "has the following entries...",
    )
    yield StreamMessageStop(usage=TokenUsage(20, 35), stop_reason="end_turn")


async def _persistent_denial_stream() -> AsyncIterator[StreamEvent]:
    """Retry response that ALSO denies — model dug in. Tests the
    1-retry cap."""
    yield StreamTextDelta(
        text="Sorry, I cannot browse the web from this environment.",
    )
    yield StreamMessageStop(usage=TokenUsage(20, 25), stop_reason="end_turn")


def _make_runtime(
    tmp_path: Path,
    provider: _MockProvider,
    *,
    retry_on_denial: bool,
    reminder_after_each_call: bool = False,
) -> ConversationRuntime:
    """Build a runtime with a flag-tuned profile.

    ``reminder_after_each_call`` defaults to False here (different
    from the production default) to keep the integration tests
    focused on Mechanism C — Mechanism A's reminder messages would
    add noise to the session history assertions otherwise.
    """
    registry = ToolRegistry()
    registry.register(_FakeReadTool())

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
    runtime._model_profile = ModelProfile(
        retry_on_denial=retry_on_denial,
        reminder_after_each_call=reminder_after_each_call,
    )
    return runtime


# =============================================================================
# Test scenarios
# =============================================================================


class TestRetryOnDenial:
    @pytest.mark.asyncio
    async def test_retry_replaces_denial_with_substantive_response(
        self, tmp_path: Path,
    ) -> None:
        """Provider scripted to: tool_call → denial → substantive.
        With ``retry_on_denial=True`` the runtime detects the denial
        after the second call, injects a reminder, makes a third
        call, and the substantive response reaches the caller."""
        provider = _MockProvider([
            _tool_call_stream(),
            _denial_stream(),
            _substantive_stream(),
        ])
        runtime = _make_runtime(
            tmp_path, provider, retry_on_denial=True,
        )

        events = []
        async for event in runtime.run_turn("read /tmp/f"):
            events.append(event)

        # Three provider calls: tool call → denial → retry.
        assert provider._call_count == 3

        # The substantive text reached the caller; the denial did NOT
        # because it was buffered and discarded.
        text_deltas = [e for e in events if isinstance(e, StreamTextDelta)]
        all_text = "".join(e.text for e in text_deltas)
        assert "Based on the file contents above" in all_text
        assert "don't have access" not in all_text

    @pytest.mark.asyncio
    async def test_retry_fires_only_after_recent_tool_call(
        self, tmp_path: Path,
    ) -> None:
        """No tool was called this turn — the model just responds.
        Even if its content matches a denial pattern, the gate
        ``has_recent_tool_call`` blocks the retry. The original
        text reaches the caller as written."""

        async def _denial_no_tool_stream() -> AsyncIterator[StreamEvent]:
            yield StreamTextDelta(
                text="I cannot browse the web — I lack internet access.",
            )
            yield StreamMessageStop(
                usage=TokenUsage(10, 15), stop_reason="end_turn",
            )

        provider = _MockProvider([_denial_no_tool_stream()])
        runtime = _make_runtime(
            tmp_path, provider, retry_on_denial=True,
        )

        events = []
        async for event in runtime.run_turn("are you online?"):
            events.append(event)

        # Single provider call — no retry triggered.
        assert provider._call_count == 1
        text = "".join(
            e.text for e in events if isinstance(e, StreamTextDelta)
        )
        assert "I cannot browse" in text

    @pytest.mark.asyncio
    async def test_retry_capped_at_one_persistent_denial(
        self, tmp_path: Path,
    ) -> None:
        """Both the original AND the retry produce a denial. The cap
        kicks in: we don't retry a third time. The retry's denial
        still reaches the caller because we already burned the cap.
        """
        provider = _MockProvider([
            _tool_call_stream(),
            _denial_stream(),
            _persistent_denial_stream(),
        ])
        runtime = _make_runtime(
            tmp_path, provider, retry_on_denial=True,
        )

        events = []
        async for event in runtime.run_turn("read /tmp/f"):
            events.append(event)

        # Three calls — tool, denial, retry. NOT four.
        assert provider._call_count == 3

        # The persistent-denial text reaches the caller (cap reached).
        text = "".join(
            e.text for e in events if isinstance(e, StreamTextDelta)
        )
        assert "cannot browse the web" in text


class TestRetryDisabledByFlag:
    @pytest.mark.asyncio
    async def test_no_retry_when_flag_off_even_on_clear_denial(
        self, tmp_path: Path,
    ) -> None:
        """Flag off — denial pattern reaches user verbatim and only
        2 provider calls happen (tool + denial)."""
        provider = _MockProvider([
            _tool_call_stream(),
            _denial_stream(),
        ])
        runtime = _make_runtime(
            tmp_path, provider, retry_on_denial=False,
        )

        events = []
        async for event in runtime.run_turn("read /tmp/f"):
            events.append(event)

        assert provider._call_count == 2
        text = "".join(
            e.text for e in events if isinstance(e, StreamTextDelta)
        )
        assert "I don't have access to news APIs" in text

    @pytest.mark.asyncio
    async def test_streaming_unbuffered_when_flag_off(
        self, tmp_path: Path,
    ) -> None:
        """When the flag is off, text deltas reach the caller as
        individual events (not buffered + concatenated). Verifies the
        UX trade-off only applies to opt-in profiles."""

        async def _multi_chunk_stream() -> AsyncIterator[StreamEvent]:
            yield StreamTextDelta(text="Chunk A. ")
            yield StreamTextDelta(text="Chunk B. ")
            yield StreamTextDelta(text="Chunk C.")
            yield StreamMessageStop(
                usage=TokenUsage(10, 15), stop_reason="end_turn",
            )

        provider = _MockProvider([_multi_chunk_stream()])
        runtime = _make_runtime(
            tmp_path, provider, retry_on_denial=False,
        )

        events = []
        async for event in runtime.run_turn("hi"):
            events.append(event)

        text_deltas = [e for e in events if isinstance(e, StreamTextDelta)]
        # 3 chunks reach the caller as separate events.
        assert len(text_deltas) == 3
        assert text_deltas[0].text == "Chunk A. "
        assert text_deltas[1].text == "Chunk B. "
        assert text_deltas[2].text == "Chunk C."


class TestStreamingUx:
    @pytest.mark.asyncio
    async def test_buffered_then_one_shot_when_flag_on_no_retry(
        self, tmp_path: Path,
    ) -> None:
        """Flag ON, content does NOT match a denial pattern. Text was
        buffered during streaming; the runtime flushes it as a single
        ``StreamTextDelta`` at end of turn — "render in one shot"."""

        async def _multi_chunk_clean_stream() -> AsyncIterator[StreamEvent]:
            yield StreamTextDelta(text="Chunk A. ")
            yield StreamTextDelta(text="Chunk B.")
            yield StreamMessageStop(
                usage=TokenUsage(10, 15), stop_reason="end_turn",
            )

        provider = _MockProvider([_multi_chunk_clean_stream()])
        runtime = _make_runtime(
            tmp_path, provider, retry_on_denial=True,
        )

        events = []
        async for event in runtime.run_turn("hi"):
            events.append(event)

        text_deltas = [e for e in events if isinstance(e, StreamTextDelta)]
        # All chunks combined into one event.
        assert len(text_deltas) == 1
        assert text_deltas[0].text == "Chunk A. Chunk B."


class TestSessionHistoryAfterRetry:
    @pytest.mark.asyncio
    async def test_retry_reminder_lands_in_session_history(
        self, tmp_path: Path,
    ) -> None:
        """After a retry fires, the injected continuation reminder
        should appear in session history between the denial assistant
        message and the substantive retry response."""
        provider = _MockProvider([
            _tool_call_stream(),
            _denial_stream(),
            _substantive_stream(),
        ])
        runtime = _make_runtime(
            tmp_path, provider, retry_on_denial=True,
        )

        async for _ in runtime.run_turn("read /tmp/f"):
            pass

        # Find the reminder text in any user-role message.
        msgs = runtime.session.messages
        found_reminder = False
        for msg in msgs:
            if msg.role != "user":
                continue
            for block in msg.content:
                if (
                    isinstance(block, TextBlock)
                    and "Your previous response denied a capability"
                    in block.text
                ):
                    found_reminder = True
                    break
        assert found_reminder, (
            "expected continuation reminder in session history"
        )


class TestCostMeterDoubleCount:
    @pytest.mark.asyncio
    async def test_two_provider_calls_counted_under_retry(
        self, tmp_path: Path,
    ) -> None:
        """Spec §3.4 — retried turns count as 2 provider calls in the
        cost meter; no special accounting. ``provider._call_count`` is
        the proxy here."""
        provider = _MockProvider([
            _tool_call_stream(),
            _denial_stream(),
            _substantive_stream(),
        ])
        runtime = _make_runtime(
            tmp_path, provider, retry_on_denial=True,
        )
        async for _ in runtime.run_turn("read /tmp/f"):
            pass
        # tool_call (1) + denial (1) + retry (1) = 3 provider calls.
        # Without retry the same scenario would be 2.
        assert provider._call_count == 3
