"""Tests for v2.12.0 — malformed-tool-call retry mechanism.

Sister mechanism to v2.11.0 ``empty_compile_retry`` (in
``test_empty_compile_retry_v211.py``) and v14 mech-C
``retry_on_denial`` (in ``test_denial_retry_loop.py``).

Triggered when **iter 0** ends with the model having emitted a
``<tool_call>...</tool_call>`` wrapper that ``parse_tool_calls``
rejected — no parsed_calls, no native_tool_list, no substantive
text outside the wrapper, ``stop_reason="stop"``/``"end_turn"``,
``out_tokens > 0``. v2.11.1's parser hotfix surfaces this failure
mode: rejection on iter 0 leaves the agent loop with nothing to
dispatch and silence to render.

Drives :class:`ConversationRuntime` end-to-end through a mocked
provider that returns scripted streams. Verifies:

* trigger fires only when ALL gating conditions hold (and the v2.11
  ``empty_compile_retry`` trigger does NOT double-fire),
* 1-retry cap, graceful fallthrough on persistent malformed output,
* profile flag off → v2.11.1 byte-parity (no retry, advisory fires),
* the injected ``<system-reminder>`` is well-formed XML and includes
  the canonical ``<arg_key>K</arg_key><arg_value>V</arg_value>``
  example envelope,
* structured telemetry log line shape mirrors v2.11 / v14 mech-C.
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

# Canonical malformed envelope — args JSON crammed into ``<arg_key>``
# instead of split into ``<arg_key>K</arg_key><arg_value>V</arg_value>``
# pairs. v2.11.1's parser hotfix correctly rejects this shape; v2.12
# detects the rejection and triggers a format-correction retry.
_MALFORMED_TOOL_CALL = (
    '<tool_call>read_file<arg_key>{"path":"/tmp/f"}</arg_key>'
    '</tool_call>'
)


async def _malformed_tool_call_stream() -> AsyncIterator[StreamEvent]:
    """Iter 0 reproducer: model emits a parser-rejected tool_call
    wrapper and nothing else useful."""
    yield StreamTextDelta(text=_MALFORMED_TOOL_CALL)
    yield StreamMessageStop(usage=TokenUsage(15, 54), stop_reason="stop")


async def _malformed_tool_call_end_turn_stream() -> AsyncIterator[StreamEvent]:
    """Same shape but ``stop_reason="end_turn"`` — both reasons
    must trigger the retry per spec."""
    yield StreamTextDelta(text=_MALFORMED_TOOL_CALL)
    yield StreamMessageStop(usage=TokenUsage(15, 54), stop_reason="end_turn")


async def _well_formed_tool_call_stream(
    text: str = "Here are the news.",
) -> AsyncIterator[StreamEvent]:
    """Retry response: model takes the format-correction reminder
    and produces a coherent text answer (treated as final answer
    on iter 1 since there are no tool calls)."""
    yield StreamTextDelta(text=text)
    yield StreamMessageStop(
        usage=TokenUsage(40, 30), stop_reason="end_turn",
    )


async def _empty_iter0_stream() -> AsyncIterator[StreamEvent]:
    """Iter 0 with no text and no tool_call markers — different
    bug class. v2.12 must NOT fire (no malformed wrapper to detect)."""
    yield StreamMessageStop(usage=TokenUsage(15, 5), stop_reason="stop")


async def _persistent_malformed_stream() -> AsyncIterator[StreamEvent]:
    """Retry response that ALSO emits a malformed wrapper. Used to
    exercise the 1-retry cap + graceful-degradation diagnostic."""
    yield StreamTextDelta(text=_MALFORMED_TOOL_CALL)
    yield StreamMessageStop(usage=TokenUsage(40, 54), stop_reason="stop")


async def _malformed_with_text_stream() -> AsyncIterator[StreamEvent]:
    """Malformed wrapper + a real text answer alongside it. The
    spec exempts this case — model ALREADY answered the user, no
    point retrying just to fix the parser shape."""
    yield StreamTextDelta(
        text=(
            "The top news today: A, B, C. " + _MALFORMED_TOOL_CALL
        ),
    )
    yield StreamMessageStop(usage=TokenUsage(15, 30), stop_reason="end_turn")


async def _zero_out_tokens_stream() -> AsyncIterator[StreamEvent]:
    """Provider returned 0 output tokens. Genuine zero-byte
    response is a different failure class — v2.12 must NOT fire
    (the trigger requires ``out_tokens > 0``)."""
    yield StreamTextDelta(text=_MALFORMED_TOOL_CALL)
    yield StreamMessageStop(usage=TokenUsage(15, 0), stop_reason="stop")


async def _malformed_then_tool_use_stop_stream() -> AsyncIterator[StreamEvent]:
    """Malformed wrapper + ``stop_reason="tool_use"`` (mid-loop).
    The trigger requires natural-stop semantics; ``tool_use`` means
    the provider thinks more iterations are coming."""
    yield StreamTextDelta(text=_MALFORMED_TOOL_CALL)
    yield StreamMessageStop(
        usage=TokenUsage(15, 54), stop_reason="tool_use",
    )


# ---- Runtime factory ------------------------------------------------------


def _make_runtime(
    tmp_path: Path,
    provider: _MockProvider,
    *,
    malformed_tool_retry: bool,
    empty_compile_retry: bool = False,
    malformed_tool_retry_message: str = "",
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
        malformed_tool_retry=malformed_tool_retry,
        empty_compile_retry=empty_compile_retry,
        malformed_tool_retry_message=malformed_tool_retry_message,
        reminder_after_each_call=reminder_after_each_call,
    )
    return runtime


# =============================================================================
# Trigger fires when all conditions hold
# =============================================================================


class TestTriggerFires:
    @pytest.mark.asyncio
    async def test_retry_replaces_malformed_with_real_answer(
        self, tmp_path: Path,
    ) -> None:
        """All gating conditions hold → retry fires → the second
        call's substantive response reaches the caller. Spec test #1."""
        provider = _MockProvider([
            _malformed_tool_call_stream(),
            _well_formed_tool_call_stream(text="Final answer here."),
        ])
        runtime = _make_runtime(
            tmp_path, provider, malformed_tool_retry=True,
        )

        events = []
        async for event in runtime.run_turn("show me the news"):
            events.append(event)

        # Two provider calls: malformed → retry.
        assert provider._call_count == 2

        text_deltas = [e for e in events if isinstance(e, StreamTextDelta)]
        all_text = "".join(e.text for e in text_deltas)
        assert "Final answer here." in all_text

    @pytest.mark.asyncio
    async def test_trigger_fires_on_end_turn_stop_reason_too(
        self, tmp_path: Path,
    ) -> None:
        """Spec: trigger fires for both ``stop`` and ``end_turn``
        (NOT ``tool_use``)."""
        provider = _MockProvider([
            _malformed_tool_call_end_turn_stream(),
            _well_formed_tool_call_stream(text="ok"),
        ])
        runtime = _make_runtime(
            tmp_path, provider, malformed_tool_retry=True,
        )

        async for _ in runtime.run_turn("show me the news"):
            pass

        assert provider._call_count == 2


# =============================================================================
# Trigger gating — each gate exercised in isolation
# =============================================================================


class TestTriggerGates:
    @pytest.mark.asyncio
    async def test_no_retry_on_iteration_one(
        self, tmp_path: Path,
    ) -> None:
        """Iter 1 with the malformed shape is v2.11's territory
        (sort of — actually different gate, but the v2.12 trigger
        explicitly requires iter 0). Must not double-fire across
        the two mechanisms. Spec test #2."""

        async def _tool_use_stream() -> AsyncIterator[StreamEvent]:
            # iter 0: normal native tool call → tool_use stop
            from llm_code.api.types import (
                StreamToolUseInputDelta,
                StreamToolUseStart,
            )
            yield StreamToolUseStart(id="c1", name="read_file")
            yield StreamToolUseInputDelta(
                id="c1", partial_json='{"path":"/tmp/f"}',
            )
            yield StreamMessageStop(
                usage=TokenUsage(20, 10), stop_reason="tool_use",
            )

        # iter 1: model emits malformed wrapper. v2.12 must skip
        # (iter != 0); v2.11 with retry off must also skip.
        provider = _MockProvider([
            _tool_use_stream(),
            _malformed_tool_call_stream(),
        ])
        runtime = _make_runtime(
            tmp_path,
            provider,
            malformed_tool_retry=True,
            empty_compile_retry=False,
        )

        async for _ in runtime.run_turn("read /tmp/f"):
            pass

        # Two calls only — no third retry call from v2.12.
        assert provider._call_count == 2

    @pytest.mark.asyncio
    async def test_no_retry_when_parsed_calls_nonempty(
        self, tmp_path: Path,
    ) -> None:
        """Well-formed tool call on iter 0 — parser succeeds, no
        retry. Spec test #3."""

        async def _well_formed_native_tool() -> AsyncIterator[StreamEvent]:
            from llm_code.api.types import (
                StreamToolUseInputDelta,
                StreamToolUseStart,
            )
            yield StreamToolUseStart(id="c1", name="read_file")
            yield StreamToolUseInputDelta(
                id="c1", partial_json='{"path":"/tmp/f"}',
            )
            yield StreamMessageStop(
                usage=TokenUsage(20, 10), stop_reason="tool_use",
            )

        async def _final_answer() -> AsyncIterator[StreamEvent]:
            yield StreamTextDelta(text="answer")
            yield StreamMessageStop(
                usage=TokenUsage(20, 5), stop_reason="end_turn",
            )

        provider = _MockProvider([
            _well_formed_native_tool(),
            _final_answer(),
        ])
        runtime = _make_runtime(
            tmp_path, provider, malformed_tool_retry=True,
        )

        async for _ in runtime.run_turn("read /tmp/f"):
            pass

        # Two calls: tool + answer. No retry inserted.
        assert provider._call_count == 2

    @pytest.mark.asyncio
    async def test_no_retry_when_assistant_text_was_added(
        self, tmp_path: Path,
    ) -> None:
        """Model produced a real text answer alongside a malformed
        wrapper. The spec exempts this case — user already saw an
        answer. Spec test #4."""
        provider = _MockProvider([_malformed_with_text_stream()])
        runtime = _make_runtime(
            tmp_path, provider, malformed_tool_retry=True,
        )

        events = []
        async for event in runtime.run_turn("show news"):
            events.append(event)

        # Single provider call — text was present, no retry.
        assert provider._call_count == 1

    @pytest.mark.asyncio
    async def test_no_retry_when_no_tool_call_marker(
        self, tmp_path: Path,
    ) -> None:
        """Iter 0 with no ``<tool_call>`` marker at all — different
        bug class. Spec test #5."""
        provider = _MockProvider([_empty_iter0_stream()])
        runtime = _make_runtime(
            tmp_path, provider, malformed_tool_retry=True,
        )

        async for _ in runtime.run_turn("hello"):
            pass

        assert provider._call_count == 1

    @pytest.mark.asyncio
    async def test_no_retry_when_stop_reason_tool_use(
        self, tmp_path: Path,
    ) -> None:
        """Mid-loop ``stop_reason="tool_use"`` — provider thinks
        more iterations are coming. v2.12 only fires on a natural
        stop. Spec test #6."""
        provider = _MockProvider([
            _malformed_then_tool_use_stop_stream(),
        ])
        runtime = _make_runtime(
            tmp_path, provider, malformed_tool_retry=True,
        )

        async for _ in runtime.run_turn("show news"):
            pass

        # Single provider call. The malformed wrapper + tool_use
        # stop produces a degenerate iteration that doesn't loop
        # forever — see _consecutive_empty_responses guard.
        assert provider._call_count == 1

    @pytest.mark.asyncio
    async def test_no_retry_when_zero_out_tokens(
        self, tmp_path: Path,
    ) -> None:
        """Genuine zero-byte response — different failure class
        (provider hung, not a malformed envelope). v2.12 requires
        ``out_tokens > 0`` so the existing empty-response advisory
        path stays in charge."""
        provider = _MockProvider([_zero_out_tokens_stream()])
        runtime = _make_runtime(
            tmp_path, provider, malformed_tool_retry=True,
        )

        async for _ in runtime.run_turn("show news"):
            pass

        assert provider._call_count == 1


# =============================================================================
# 1-retry cap and graceful degradation
# =============================================================================


class TestRetryCap:
    @pytest.mark.asyncio
    async def test_cap_at_one_retry_per_turn(
        self, tmp_path: Path,
    ) -> None:
        """Both the original AND the retry produce malformed
        wrappers. The cap kicks in: we don't retry a third time.
        Spec test #7."""
        provider = _MockProvider([
            _malformed_tool_call_stream(),
            _persistent_malformed_stream(),
        ])
        runtime = _make_runtime(
            tmp_path, provider, malformed_tool_retry=True,
        )

        async for _ in runtime.run_turn("show news"):
            pass

        # Two calls — original + 1 retry. NOT three.
        assert provider._call_count == 2

    @pytest.mark.asyncio
    async def test_persistent_malformed_logs_diagnostic(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Cap reached + still malformed → structured warning logged
        (mirrors v2.11 ``empty_compile_retry_failed`` shape). Spec
        test #9."""
        provider = _MockProvider([
            _malformed_tool_call_stream(),
            _persistent_malformed_stream(),
        ])
        runtime = _make_runtime(
            tmp_path, provider, malformed_tool_retry=True,
        )

        with caplog.at_level(logging.WARNING):
            async for _ in runtime.run_turn("show news"):
                pass

        diag_logs = [
            r for r in caplog.records
            if "malformed_tool_retry_failed" in r.getMessage()
        ]
        assert len(diag_logs) == 1
        assert "malformed_persisted_after_retry" in diag_logs[0].getMessage()

    @pytest.mark.asyncio
    async def test_retry_then_real_answer_no_advisory(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Spec test #8: malformed → retry → coherent text → no
        ``malformed_tool_retry_failed`` log line emitted."""
        provider = _MockProvider([
            _malformed_tool_call_stream(),
            _well_formed_tool_call_stream(text="Real answer."),
        ])
        runtime = _make_runtime(
            tmp_path, provider, malformed_tool_retry=True,
        )

        with caplog.at_level(logging.WARNING):
            async for _ in runtime.run_turn("show news"):
                pass

        diag_logs = [
            r for r in caplog.records
            if "malformed_tool_retry_failed" in r.getMessage()
        ]
        assert diag_logs == []


# =============================================================================
# Profile flag off → v2.11.1 byte-parity
# =============================================================================


class TestFlagOff:
    @pytest.mark.asyncio
    async def test_no_retry_when_flag_off(
        self, tmp_path: Path,
    ) -> None:
        """Flag off — malformed response reaches user as-is and only
        1 provider call happens. Spec test #10."""
        provider = _MockProvider([_malformed_tool_call_stream()])
        runtime = _make_runtime(
            tmp_path, provider, malformed_tool_retry=False,
        )

        async for _ in runtime.run_turn("show news"):
            pass

        # No retry — only the original 1 call.
        assert provider._call_count == 1

    @pytest.mark.asyncio
    async def test_no_retry_log_when_flag_off(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Flag off → no ``malformed_tool_retry`` log line emitted
        even when the underlying conditions otherwise match. v2.11.1
        observers see no new noise."""
        provider = _MockProvider([_malformed_tool_call_stream()])
        runtime = _make_runtime(
            tmp_path, provider, malformed_tool_retry=False,
        )

        with caplog.at_level(logging.WARNING):
            async for _ in runtime.run_turn("show news"):
                pass

        retry_logs = [
            r for r in caplog.records
            if "tool_consumption: malformed_tool_retry" in r.getMessage()
        ]
        assert retry_logs == []


# =============================================================================
# Telemetry log shape (mirrors v2.11 / v14 mech-C)
# =============================================================================


class TestTelemetry:
    @pytest.mark.asyncio
    async def test_log_emitted_on_retry_path(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Spec test #12: when the retry fires, exactly one
        ``tool_consumption: malformed_tool_retry`` warning is logged
        with iter / retry_count / stop_reason / out_tokens fields."""
        provider = _MockProvider([
            _malformed_tool_call_stream(),
            _well_formed_tool_call_stream(text="ok"),
        ])
        runtime = _make_runtime(
            tmp_path, provider, malformed_tool_retry=True,
        )

        with caplog.at_level(logging.WARNING):
            async for _ in runtime.run_turn("show news"):
                pass

        retry_logs = [
            r for r in caplog.records
            if r.getMessage().startswith(
                "tool_consumption: malformed_tool_retry "
            )
        ]
        assert len(retry_logs) == 1
        msg = retry_logs[0].getMessage()
        assert "iter=" in msg
        assert "retry_count=" in msg
        assert "tool_calls_this_turn=" in msg
        assert "stop_reason=" in msg
        assert "out_tokens=" in msg

    @pytest.mark.asyncio
    async def test_log_not_emitted_on_no_retry_path(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """No retry path → counter must NOT increment. Spec test
        #12 (negative half)."""
        provider = _MockProvider([_empty_iter0_stream()])
        runtime = _make_runtime(
            tmp_path, provider, malformed_tool_retry=True,
        )

        with caplog.at_level(logging.WARNING):
            async for _ in runtime.run_turn("hi"):
                pass

        retry_logs = [
            r for r in caplog.records
            if r.getMessage().startswith(
                "tool_consumption: malformed_tool_retry "
            )
        ]
        assert retry_logs == []


# =============================================================================
# Reminder XML well-formed + canonical example present
# =============================================================================


class TestReminderShape:
    def test_default_reminder_is_well_formed_xml(self) -> None:
        """The default reminder must parse cleanly as XML so models
        with strict chat templates don't choke on malformed escapes.
        Spec test #11."""
        from llm_code.runtime.conversation import (
            _DEFAULT_MALFORMED_TOOL_RETRY_REMINDER,
        )
        # ``<system-reminder>`` is a known directive tag; parse it
        # directly. The body contains a ``<tool_call>`` example that
        # is itself well-formed XML (TOOL_NAME + paired arg_key /
        # arg_value tags), so the entire reminder is valid XML.
        root = ET.fromstring(_DEFAULT_MALFORMED_TOOL_RETRY_REMINDER)
        assert root.tag == "system-reminder"

    def test_default_reminder_includes_canonical_example(self) -> None:
        """Spec test #11 (continued): the canonical
        ``<arg_key>K</arg_key><arg_value>V</arg_value>`` example must
        be embedded so the model has a concrete shape to copy."""
        from llm_code.runtime.conversation import (
            _DEFAULT_MALFORMED_TOOL_RETRY_REMINDER,
        )
        body = _DEFAULT_MALFORMED_TOOL_RETRY_REMINDER
        # Canonical envelope structure
        assert "<tool_call>" in body
        assert "<arg_key>" in body
        assert "<arg_value>" in body
        # The actionable instruction — explicitly tells the model
        # NOT to put JSON inside <arg_key>.
        assert "Do NOT" in body
        assert "JSON" in body

    @pytest.mark.asyncio
    async def test_empty_message_falls_back_to_default(
        self,
        tmp_path: Path,
    ) -> None:
        """Spec test #13 (default-empty path): an empty
        ``malformed_tool_retry_message`` falls back to the canned
        default. Verify by checking session history after the retry
        — the injected reminder body should match the canned
        default verbatim."""
        from llm_code.api.types import TextBlock
        from llm_code.runtime.conversation import (
            _DEFAULT_MALFORMED_TOOL_RETRY_REMINDER,
        )

        provider = _MockProvider([
            _malformed_tool_call_stream(),
            _well_formed_tool_call_stream(text="ok"),
        ])
        runtime = _make_runtime(
            tmp_path,
            provider,
            malformed_tool_retry=True,
            malformed_tool_retry_message="",  # explicit empty
        )

        async for _ in runtime.run_turn("show news"):
            pass

        # Walk session history; find the system-reminder user
        # message injected by the retry path.
        reminder_texts = []
        for msg in runtime.session.messages:
            if msg.role != "user":
                continue
            for block in msg.content:
                if (
                    isinstance(block, TextBlock)
                    and "system-reminder" in block.text
                    and "tool call that the parser" in block.text
                ):
                    reminder_texts.append(block.text)
        assert len(reminder_texts) == 1
        assert reminder_texts[0] == _DEFAULT_MALFORMED_TOOL_RETRY_REMINDER

    @pytest.mark.asyncio
    async def test_explicit_message_overrides_default(
        self,
        tmp_path: Path,
    ) -> None:
        """Spec test #13 (override path): a non-empty
        ``malformed_tool_retry_message`` overrides the canned
        default. The override is used verbatim — no template
        formatting, since v2.12's reminder doesn't take a
        ``{tool_calls}`` placeholder (iter 0 always has 0 calls)."""
        from llm_code.api.types import TextBlock

        custom = (
            "<system-reminder>custom format please.</system-reminder>"
        )
        provider = _MockProvider([
            _malformed_tool_call_stream(),
            _well_formed_tool_call_stream(text="ok"),
        ])
        runtime = _make_runtime(
            tmp_path,
            provider,
            malformed_tool_retry=True,
            malformed_tool_retry_message=custom,
        )

        async for _ in runtime.run_turn("show news"):
            pass

        # Walk session history; find the injected reminder.
        reminder_texts = []
        for msg in runtime.session.messages:
            if msg.role != "user":
                continue
            for block in msg.content:
                if (
                    isinstance(block, TextBlock)
                    and "custom format please" in block.text
                ):
                    reminder_texts.append(block.text)
        assert len(reminder_texts) == 1
        assert reminder_texts[0] == custom
        # The override must still parse as XML.
        root = ET.fromstring(reminder_texts[0])
        assert root.tag == "system-reminder"


# =============================================================================
# Mutual exclusivity vs v2.11 empty_compile_retry
# =============================================================================


class TestMutualExclusivity:
    @pytest.mark.asyncio
    async def test_both_flags_on_no_double_fire_v211(
        self, tmp_path: Path,
    ) -> None:
        """When both ``empty_compile_retry`` AND
        ``malformed_tool_retry`` are on (canonical GLM config after
        v2.12), the two retry mechanisms must not double-fire on
        the iter-0 malformed shape: only v2.12 fires (iter == 0
        gates v2.11 out)."""
        provider = _MockProvider([
            _malformed_tool_call_stream(),
            _well_formed_tool_call_stream(text="answer"),
        ])
        runtime = _make_runtime(
            tmp_path,
            provider,
            malformed_tool_retry=True,
            empty_compile_retry=True,
        )

        async for _ in runtime.run_turn("show news"):
            pass

        # Two provider calls — single retry from v2.12, NOT a
        # cumulative 3 calls.
        assert provider._call_count == 2
