"""Tests for v2.11.0 — empty-compile retry mechanism.

Sister mechanism to v14 mech-C ``retry_on_denial`` (in
``test_denial_retry_loop.py``). Triggered when an iteration ends
with **0 visible text + 0 (or negligible) thinking + stop_reason
"stop"/"end_turn"** AND the model has already invoked tools
earlier in this turn — the canonical "silent compile" failure
mode that surfaced on a real GLM-5.1 smoke test.

Drives :class:`ConversationRuntime` end-to-end through a mocked
provider that returns scripted streams. Verifies:

* trigger fires only when ALL gating conditions hold,
* 1-retry cap, graceful fallthrough on persistent empty,
* profile flag off → v2.10 byte-parity (no retry, advisory fires),
* the injected ``<system-reminder>`` is well-formed XML,
* structured telemetry log line shape mirrors v14 mech-C.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import AsyncIterator
from xml.etree import ElementTree as ET

import pytest

from llm_code.api.types import (
    MessageRequest,
    StreamEvent,
    StreamMessageStop,
    StreamTextDelta,
    StreamThinkingDelta,
    StreamToolUseInputDelta,
    StreamToolUseStart,
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


# ---- Stream stubs ---------------------------------------------------------


async def _tool_call_stream(call_id: str = "call1") -> AsyncIterator[StreamEvent]:
    """Decision-iter response: model emits a tool call (no text)."""
    yield StreamToolUseStart(id=call_id, name="read_file")
    yield StreamToolUseInputDelta(
        id=call_id, partial_json='{"path":"/tmp/f"}',
    )
    yield StreamMessageStop(usage=TokenUsage(20, 10), stop_reason="tool_use")


async def _empty_compile_stream() -> AsyncIterator[StreamEvent]:
    """Silent-compile failure: 0 text + 0 thinking + stop_reason='stop'.

    GLM-5.1's reproducer in production. The trigger condition for
    the v2.11 mechanism."""
    yield StreamMessageStop(usage=TokenUsage(15, 220), stop_reason="stop")


async def _empty_compile_end_turn_stream() -> AsyncIterator[StreamEvent]:
    """Same as above but stop_reason='end_turn' — both values must
    trigger the retry per spec."""
    yield StreamMessageStop(usage=TokenUsage(15, 220), stop_reason="end_turn")


async def _substantive_compile_stream() -> AsyncIterator[StreamEvent]:
    """Retry response: model takes the reminder and produces a
    coherent summary."""
    yield StreamTextDelta(
        text="Here are the three top news stories: 1) ... 2) ... 3) ...",
    )
    yield StreamMessageStop(usage=TokenUsage(20, 35), stop_reason="end_turn")


async def _empty_with_thinking_stream() -> AsyncIterator[StreamEvent]:
    """Thinking present + 0 text. The thinking gate (>= 8 chars
    after strip) must block the retry — the renderer's separate
    'thinking → answer' fallback handles this case already."""
    yield StreamThinkingDelta(text="The user asked for top news; let me think...")
    yield StreamMessageStop(usage=TokenUsage(15, 50), stop_reason="stop")


async def _empty_first_iter_stream() -> AsyncIterator[StreamEvent]:
    """Iter-0 response with no text and no tool calls — different bug
    class. The v2.11 retry must NOT fire on iter 0 (decision-iter
    is exempt)."""
    yield StreamMessageStop(usage=TokenUsage(15, 5), stop_reason="stop")


async def _persistent_empty_stream() -> AsyncIterator[StreamEvent]:
    """Retry response that ALSO produces empty output. Tests the cap."""
    yield StreamMessageStop(usage=TokenUsage(20, 10), stop_reason="stop")


# ---- Runtime factory ------------------------------------------------------


def _make_runtime(
    tmp_path: Path,
    provider: _MockProvider,
    *,
    empty_compile_retry: bool,
    reminder_after_each_call: bool = False,
) -> ConversationRuntime:
    """Build a runtime with a flag-tuned profile.

    ``reminder_after_each_call`` defaults to False (different from
    the production default) so the integration tests don't accumulate
    Mechanism A noise in session history.
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
        empty_compile_retry=empty_compile_retry,
        reminder_after_each_call=reminder_after_each_call,
    )
    return runtime


# =============================================================================
# Trigger fires when all conditions hold
# =============================================================================


class TestTriggerFires:
    @pytest.mark.asyncio
    async def test_retry_replaces_empty_with_substantive_response(
        self, tmp_path: Path,
    ) -> None:
        """All five gating conditions hold → retry fires → substantive
        response from the retried call reaches the caller."""
        provider = _MockProvider([
            _tool_call_stream(),
            _empty_compile_stream(),
            _substantive_compile_stream(),
        ])
        runtime = _make_runtime(
            tmp_path, provider, empty_compile_retry=True,
        )

        events = []
        async for event in runtime.run_turn("read /tmp/f"):
            events.append(event)

        # Three provider calls: tool call → empty → retry.
        assert provider._call_count == 3

        text_deltas = [e for e in events if isinstance(e, StreamTextDelta)]
        all_text = "".join(e.text for e in text_deltas)
        assert "top news stories" in all_text

    @pytest.mark.asyncio
    async def test_trigger_fires_on_end_turn_stop_reason_too(
        self, tmp_path: Path,
    ) -> None:
        """Spec: trigger fires for both ``stop`` and ``end_turn``
        stop reasons (NOT ``tool_use``)."""
        provider = _MockProvider([
            _tool_call_stream(),
            _empty_compile_end_turn_stream(),
            _substantive_compile_stream(),
        ])
        runtime = _make_runtime(
            tmp_path, provider, empty_compile_retry=True,
        )

        async for _ in runtime.run_turn("read /tmp/f"):
            pass

        assert provider._call_count == 3


# =============================================================================
# Trigger gating — each gate exercised in isolation
# =============================================================================


class TestTriggerGates:
    @pytest.mark.asyncio
    async def test_no_retry_on_iteration_zero(
        self, tmp_path: Path,
    ) -> None:
        """First iteration produces nothing — different bug class.
        The v2.11 retry must stay dormant; existing empty-response
        machinery in the renderer handles iter-0 silence."""
        provider = _MockProvider([_empty_first_iter_stream()])
        runtime = _make_runtime(
            tmp_path, provider, empty_compile_retry=True,
        )

        async for _ in runtime.run_turn("hello"):
            pass

        # Single provider call — no retry triggered.
        assert provider._call_count == 1

    @pytest.mark.asyncio
    async def test_no_retry_when_assistant_text_present(
        self, tmp_path: Path,
    ) -> None:
        """Model DID produce a final answer — text content present.
        The trigger must not fire (turn ends normally)."""

        async def _tool_then_text() -> AsyncIterator[StreamEvent]:
            yield StreamTextDelta(text="Top news: A, B, C.")
            yield StreamMessageStop(
                usage=TokenUsage(20, 30), stop_reason="end_turn",
            )

        provider = _MockProvider([
            _tool_call_stream(),
            _tool_then_text(),
        ])
        runtime = _make_runtime(
            tmp_path, provider, empty_compile_retry=True,
        )

        events = []
        async for event in runtime.run_turn("read /tmp/f"):
            events.append(event)

        # Two calls: tool + answer. NOT three.
        assert provider._call_count == 2
        text = "".join(
            e.text for e in events if isinstance(e, StreamTextDelta)
        )
        assert "Top news" in text

    @pytest.mark.asyncio
    async def test_no_retry_when_thinking_buffer_nonempty(
        self, tmp_path: Path,
    ) -> None:
        """Model thought (reasoning_content), just produced no
        surface text. The renderer's thinking → answer fallback
        handles this case; v2.11 retry must not duplicate that
        path."""
        provider = _MockProvider([
            _tool_call_stream(),
            _empty_with_thinking_stream(),
        ])
        runtime = _make_runtime(
            tmp_path, provider, empty_compile_retry=True,
        )

        async for _ in runtime.run_turn("read /tmp/f"):
            pass

        # Two calls — no retry, even though text is empty.
        assert provider._call_count == 2

    @pytest.mark.asyncio
    async def test_no_retry_without_prior_tool_calls(
        self, tmp_path: Path,
    ) -> None:
        """No tool was called this turn. Empty response on iter > 0
        without prior tools is a different bug class — the consecutive-
        empty nudge in conversation.py handles it."""

        async def _iter0_text_then_empty() -> AsyncIterator[StreamEvent]:
            # Iter 0: produce some text but no tool calls
            yield StreamTextDelta(text="Let me think about this.")
            yield StreamMessageStop(
                usage=TokenUsage(15, 8), stop_reason="end_turn",
            )

        provider = _MockProvider([_iter0_text_then_empty()])
        runtime = _make_runtime(
            tmp_path, provider, empty_compile_retry=True,
        )

        async for _ in runtime.run_turn("hi"):
            pass

        # Single call — no tool was called, so the gate blocks retry.
        assert provider._call_count == 1


# =============================================================================
# 1-retry cap and graceful degradation
# =============================================================================


class TestRetryCap:
    @pytest.mark.asyncio
    async def test_cap_at_one_retry_per_turn(
        self, tmp_path: Path,
    ) -> None:
        """Both the original AND the retry produce empty output.
        The cap kicks in: we don't retry a third time. The turn ends
        with the persistent-empty diagnostic logged, not a third
        provider call."""
        provider = _MockProvider([
            _tool_call_stream(),
            _empty_compile_stream(),
            _persistent_empty_stream(),
        ])
        runtime = _make_runtime(
            tmp_path, provider, empty_compile_retry=True,
        )

        async for _ in runtime.run_turn("read /tmp/f"):
            pass

        # Three calls — tool, empty, retry. NOT four.
        assert provider._call_count == 3

    @pytest.mark.asyncio
    async def test_persistent_empty_logs_diagnostic(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Cap reached + still empty → structured warning logged
        (mirrors v14 ``denial_retry_failed`` shape)."""
        provider = _MockProvider([
            _tool_call_stream(),
            _empty_compile_stream(),
            _persistent_empty_stream(),
        ])
        runtime = _make_runtime(
            tmp_path, provider, empty_compile_retry=True,
        )

        with caplog.at_level(logging.WARNING):
            async for _ in runtime.run_turn("read /tmp/f"):
                pass

        diag_logs = [
            r for r in caplog.records
            if "empty_compile_retry_failed" in r.getMessage()
        ]
        assert len(diag_logs) == 1
        assert "empty_persisted_after_retry" in diag_logs[0].getMessage()


# =============================================================================
# Profile flag off → v2.10 byte-parity
# =============================================================================


class TestFlagOff:
    @pytest.mark.asyncio
    async def test_no_retry_when_flag_off(
        self, tmp_path: Path,
    ) -> None:
        """Flag off — empty response reaches user as-is and only 2
        provider calls happen (tool + empty)."""
        provider = _MockProvider([
            _tool_call_stream(),
            _empty_compile_stream(),
        ])
        runtime = _make_runtime(
            tmp_path, provider, empty_compile_retry=False,
        )

        async for _ in runtime.run_turn("read /tmp/f"):
            pass

        # No retry — only the original 2 calls.
        assert provider._call_count == 2

    @pytest.mark.asyncio
    async def test_no_retry_log_when_flag_off(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Flag off → no ``empty_compile_retry`` log line emitted
        even when the underlying conditions otherwise match. v2.10
        observers see no new noise."""
        provider = _MockProvider([
            _tool_call_stream(),
            _empty_compile_stream(),
        ])
        runtime = _make_runtime(
            tmp_path, provider, empty_compile_retry=False,
        )

        with caplog.at_level(logging.WARNING):
            async for _ in runtime.run_turn("read /tmp/f"):
                pass

        retry_logs = [
            r for r in caplog.records
            if "tool_consumption: empty_compile_retry" in r.getMessage()
        ]
        assert retry_logs == []


# =============================================================================
# Telemetry log shape (mirrors v14 mech-C)
# =============================================================================


class TestTelemetry:
    @pytest.mark.asyncio
    async def test_log_emitted_on_retry_path(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """When the retry fires, exactly one
        ``tool_consumption: empty_compile_retry`` warning is logged
        with the running tool-call count + iteration index."""
        provider = _MockProvider([
            _tool_call_stream(),
            _empty_compile_stream(),
            _substantive_compile_stream(),
        ])
        runtime = _make_runtime(
            tmp_path, provider, empty_compile_retry=True,
        )

        with caplog.at_level(logging.WARNING):
            async for _ in runtime.run_turn("read /tmp/f"):
                pass

        retry_logs = [
            r for r in caplog.records
            if r.getMessage().startswith(
                "tool_consumption: empty_compile_retry "
            )
        ]
        assert len(retry_logs) == 1
        msg = retry_logs[0].getMessage()
        assert "tool_calls=" in msg
        assert "iteration=" in msg
        assert "stop_reason=" in msg


# =============================================================================
# Reminder XML well-formed
# =============================================================================


class TestReminderXmlShape:
    def test_default_reminder_is_well_formed_xml(self) -> None:
        """The default reminder must parse cleanly as XML so models
        with strict chat templates don't choke on malformed escapes."""
        profile = ModelProfile(empty_compile_retry=True)
        rendered = profile.empty_compile_retry_message.format(tool_calls=2)
        # ``<system-reminder>`` is a known directive tag; parse it
        # directly. ElementTree accepts the canned text iff the
        # angle-bracket content is balanced and free of stray
        # ``<`` / ``&`` outside CDATA / entities.
        root = ET.fromstring(rendered)
        assert root.tag == "system-reminder"
        body = (root.text or "").strip()
        assert "2 tool call" in body
        assert "compile your final answer" in body.lower()

    def test_custom_reminder_format_args_safe(self) -> None:
        """A custom reminder without the ``{tool_calls}`` placeholder
        still renders safely (KeyError-resilient via the runtime's
        ``except (KeyError, IndexError, ValueError)`` guard)."""
        profile = ModelProfile(
            empty_compile_retry=True,
            empty_compile_retry_message=(
                "<system-reminder>compile now.</system-reminder>"
            ),
        )
        # The raw string is what the runtime falls back to when
        # ``.format(tool_calls=...)`` raises; it must still parse.
        root = ET.fromstring(profile.empty_compile_retry_message)
        assert root.tag == "system-reminder"
