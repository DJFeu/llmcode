"""Unit tests for ``ViewStreamRenderer``.

Drives the renderer through a :class:`StubRecordingBackend` and a
hand-crafted fake runtime so every StreamEvent branch can be
exercised deterministically without standing up a real provider.

Tests are grouped by concern:

- **Construction / lifecycle** — basic shape, on_turn_start / on_turn_end
- **Text streaming** — TextDelta → StreamingMessageHandle feed/commit
- **Thinking** — explicit StreamThinkingDelta + inline <think> tag parsing
- **Tool events** — Start → Result pairing, success vs failure, fallback
- **Permission dialog** — allow, edit (valid JSON), edit (bad JSON), deny,
  dialog cancel
- **Compaction** — print_info + on_session_compaction
- **Message stop** — token counters, cost tracker, status update
- **Error paths** — runtime=None, exception mid-turn, finally flag clear
- **Post-turn diagnostics** — empty-response fallback, truncation warning
- **Skill router** — print_info when matches fire

Every test uses ``FakeRuntime`` as the StreamEvent source and a
shallow ``SimpleNamespace`` in place of ``AppState`` so individual
fields can be set without building every subsystem.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any, AsyncIterator, List, Optional
from unittest.mock import MagicMock

import pytest

from llm_code.api.types import (
    StreamCompactionDone,
    StreamCompactionStart,
    StreamMessageStop,
    StreamPermissionRequest,
    StreamTextDelta,
    StreamThinkingDelta,
    StreamToolExecResult,
    StreamToolExecStart,
    TokenUsage,
)
from llm_code.view.dialog_types import DialogCancelled
from llm_code.view.stream_renderer import ViewStreamRenderer
from llm_code.view.types import Role

from tests.test_view._stub_backend import StubRecordingBackend


# === Fake runtime ===


class FakeRuntime:
    """Lightweight stand-in for ``ConversationRuntime``.

    Only exposes the attributes and methods that ``ViewStreamRenderer``
    actually touches: ``run_turn``, ``send_permission_response``,
    ``plan_mode``, ``_skill_router``, ``_model_profile``, ``session``.
    """

    def __init__(
        self,
        events: Optional[List[Any]] = None,
        *,
        skill_router: Any = None,
        model_profile: Any = None,
    ) -> None:
        self._events = events or []
        self._skill_router = skill_router
        self._model_profile = model_profile
        self.plan_mode = False
        self.permission_responses: List[tuple[str, dict]] = []
        self.run_turn_calls: List[dict] = []
        # session.messages exists for the empty-response helper path.
        self.session = SimpleNamespace(messages=[])

    async def run_turn(
        self,
        user_input: str,
        images: Any = None,
        active_skill_content: Any = None,
    ) -> AsyncIterator[Any]:
        """Async generator matching ``ConversationRuntime.run_turn``'s shape."""
        self.run_turn_calls.append(
            dict(
                user_input=user_input,
                images=images,
                active_skill_content=active_skill_content,
            )
        )
        for ev in self._events:
            if isinstance(ev, Exception):
                raise ev
            yield ev

    def send_permission_response(
        self, action: str, **kwargs: Any,
    ) -> None:
        self.permission_responses.append((action, kwargs))


class FakeCostTracker:
    """Minimal CostTracker stand-in."""

    def __init__(self) -> None:
        self.usages: List[dict] = []
        self.total_cost_usd = 0.0

    def add_usage(
        self,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int = 0,
        cache_creation_tokens: int = 0,
    ) -> None:
        self.usages.append(
            dict(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cache_read_tokens=cache_read_tokens,
                cache_creation_tokens=cache_creation_tokens,
            )
        )
        self.total_cost_usd += 0.0001 * (input_tokens + output_tokens)

    def format_cost(self) -> str:
        return f"${self.total_cost_usd:.4f}"


def _make_state(
    *,
    events: Optional[List[Any]] = None,
    skill_router: Any = None,
    model_profile: Any = None,
) -> SimpleNamespace:
    """Build an AppState-shaped SimpleNamespace with a FakeRuntime."""
    runtime = FakeRuntime(
        events=events,
        skill_router=skill_router,
        model_profile=model_profile,
    )
    state = SimpleNamespace(
        runtime=runtime,
        cost_tracker=FakeCostTracker(),
        tool_reg=MagicMock(all_tools=lambda: []),
        input_tokens=0,
        output_tokens=0,
        last_stop_reason="unknown",
        plan_mode=False,
        context_warned=False,
    )
    return state


@pytest.fixture
def backend() -> StubRecordingBackend:
    return StubRecordingBackend()


# === Construction / basic lifecycle ===


def test_renderer_stores_view_and_state(backend: StubRecordingBackend) -> None:
    state = _make_state()
    renderer = ViewStreamRenderer(view=backend, state=state)
    assert renderer._view is backend
    assert renderer._state is state


@pytest.mark.asyncio
async def test_run_turn_without_runtime_prints_error(
    backend: StubRecordingBackend,
) -> None:
    state = SimpleNamespace(
        runtime=None,
        cost_tracker=None,
        tool_reg=None,
        input_tokens=0,
        output_tokens=0,
        last_stop_reason="unknown",
        plan_mode=False,
        context_warned=False,
    )
    renderer = ViewStreamRenderer(view=backend, state=state)
    await renderer.run_turn("hi")
    assert backend.error_lines
    assert "runtime not initialized" in backend.error_lines[0]
    # on_turn_start must NOT have fired when runtime is missing.
    assert backend.turn_starts == 0


@pytest.mark.asyncio
async def test_run_turn_fires_lifecycle_hooks(
    backend: StubRecordingBackend,
) -> None:
    state = _make_state(events=[])
    renderer = ViewStreamRenderer(view=backend, state=state)
    await renderer.run_turn("hi")
    assert backend.turn_starts == 1
    assert backend.turn_ends == 1


@pytest.mark.asyncio
async def test_run_turn_sets_and_clears_is_streaming(
    backend: StubRecordingBackend,
) -> None:
    state = _make_state(events=[])
    renderer = ViewStreamRenderer(view=backend, state=state)
    await renderer.run_turn("hi")
    # First status update sets is_streaming=True, last clears it.
    assert backend.status_updates[0].is_streaming is True
    assert backend.status_updates[-1].is_streaming is False


@pytest.mark.asyncio
async def test_run_turn_syncs_plan_mode_to_runtime(
    backend: StubRecordingBackend,
) -> None:
    state = _make_state(events=[])
    state.plan_mode = True
    renderer = ViewStreamRenderer(view=backend, state=state)
    await renderer.run_turn("hi")
    assert state.runtime.plan_mode is True


# === Text streaming ===


@pytest.mark.asyncio
async def test_text_delta_feeds_streaming_handle(
    backend: StubRecordingBackend,
) -> None:
    events = [
        StreamTextDelta(text="Hello "),
        StreamTextDelta(text="world."),
    ]
    state = _make_state(events=events)
    renderer = ViewStreamRenderer(view=backend, state=state)
    await renderer.run_turn("say hi")

    assert len(backend.streaming_handles) == 1
    handle = backend.streaming_handles[0]
    assert handle.role == Role.ASSISTANT
    assert handle.buffer == "Hello world."
    assert handle.committed is True


@pytest.mark.asyncio
async def test_empty_text_delta_does_not_create_handle(
    backend: StubRecordingBackend,
) -> None:
    events = [StreamTextDelta(text="")]
    state = _make_state(events=events)
    renderer = ViewStreamRenderer(view=backend, state=state)
    await renderer.run_turn("say hi")
    # No visible text → no streaming message handle
    assert backend.streaming_handles == []


# === Thinking ===


@pytest.mark.asyncio
async def test_explicit_thinking_delta_surfaces_as_info(
    backend: StubRecordingBackend,
) -> None:
    events = [
        StreamThinkingDelta(text="let me think... "),
        StreamThinkingDelta(text="ok done."),
        StreamTextDelta(text="Answer."),
    ]
    state = _make_state(events=events)
    renderer = ViewStreamRenderer(view=backend, state=state)
    await renderer.run_turn("q")

    # Thinking must be surfaced before the visible answer.
    thinking_infos = [
        line for line in backend.info_lines if line.startswith("[thinking:")
    ]
    assert thinking_infos
    assert "let me think" in thinking_infos[0]
    assert "ok done" in thinking_infos[0]


@pytest.mark.asyncio
async def test_thinking_with_no_visible_text_promotes_to_answer(
    backend: StubRecordingBackend,
) -> None:
    """Reasoning models that emit the whole response inside <think>
    and produce zero final text should still surface the thinking
    as the user-visible answer."""
    events = [
        StreamThinkingDelta(text="The answer is 42."),
        StreamMessageStop(
            usage=TokenUsage(input_tokens=5, output_tokens=0),
            stop_reason="end_turn",
        ),
    ]
    state = _make_state(events=events)
    renderer = ViewStreamRenderer(view=backend, state=state)
    await renderer.run_turn("what is 6*7")

    # One streaming handle holding the promoted thinking content
    assert len(backend.streaming_handles) == 1
    assert "42" in backend.streaming_handles[0].buffer


# === Tool events ===


@pytest.mark.asyncio
async def test_tool_exec_start_and_result_pair_on_same_handle(
    backend: StubRecordingBackend,
) -> None:
    events = [
        StreamToolExecStart(
            tool_name="read_file",
            tool_id="t1",
            args_summary="path=foo.py",
        ),
        StreamToolExecResult(
            tool_name="read_file",
            tool_id="t1",
            output="file contents here",
            is_error=False,
        ),
    ]
    state = _make_state(events=events)
    renderer = ViewStreamRenderer(view=backend, state=state)
    await renderer.run_turn("read foo")

    # Exactly one tool handle, committed as success
    assert len(backend.tool_event_handles) == 1
    handle = backend.tool_event_handles[0]
    assert handle.tool_name == "read_file"
    assert handle.args == {"args_summary": "path=foo.py"}
    assert handle.committed is True
    assert handle.success is True
    assert "file contents" in (handle.summary or "")


@pytest.mark.asyncio
async def test_tool_exec_failure_routes_through_commit_failure(
    backend: StubRecordingBackend,
) -> None:
    events = [
        StreamToolExecStart(
            tool_name="bash",
            tool_id="t1",
            args_summary="cmd=false",
        ),
        StreamToolExecResult(
            tool_name="bash",
            tool_id="t1",
            output="exit 1: permission denied",
            is_error=True,
        ),
    ]
    state = _make_state(events=events)
    renderer = ViewStreamRenderer(view=backend, state=state)
    await renderer.run_turn("run it")

    handle = backend.tool_event_handles[0]
    assert handle.committed is True
    assert handle.success is False
    assert "permission denied" in (handle.error or "")


@pytest.mark.asyncio
async def test_tool_result_without_matching_start_falls_back(
    backend: StubRecordingBackend,
) -> None:
    """Defensive path: Result without a tracked Start still gets
    surfaced so the output isn't silently dropped."""
    events = [
        StreamToolExecResult(
            tool_name="read_file",
            tool_id="orphan",
            output="bytes",
            is_error=False,
        ),
    ]
    state = _make_state(events=events)
    renderer = ViewStreamRenderer(view=backend, state=state)
    await renderer.run_turn("q")

    assert len(backend.tool_event_handles) == 1
    assert backend.tool_event_handles[0].committed is True


# === Permission dialog ===


@pytest.mark.asyncio
async def test_permission_request_allow_sends_allow(
    backend: StubRecordingBackend,
) -> None:
    events = [
        StreamPermissionRequest(
            tool_name="write_file",
            args_preview='{"path": "x"}',
            diff_lines=(),
            pending_files=(),
        ),
    ]
    state = _make_state(events=events)
    backend.scripted_select = ["allow"]
    renderer = ViewStreamRenderer(view=backend, state=state)
    await renderer.run_turn("edit")

    assert state.runtime.permission_responses == [("allow", {})]


@pytest.mark.asyncio
async def test_permission_request_deny_sends_deny(
    backend: StubRecordingBackend,
) -> None:
    events = [
        StreamPermissionRequest(
            tool_name="bash",
            args_preview="cmd=rm",
            diff_lines=(),
            pending_files=(),
        ),
    ]
    state = _make_state(events=events)
    backend.scripted_select = ["deny"]
    renderer = ViewStreamRenderer(view=backend, state=state)
    await renderer.run_turn("rm")

    assert state.runtime.permission_responses == [("deny", {})]


@pytest.mark.asyncio
async def test_permission_request_edit_valid_json_routes_edited_args(
    backend: StubRecordingBackend,
) -> None:
    events = [
        StreamPermissionRequest(
            tool_name="write_file",
            args_preview='{"path": "a"}',
            diff_lines=(),
            pending_files=(),
        ),
    ]
    state = _make_state(events=events)
    backend.scripted_select = ["edit"]
    backend.scripted_text = ['{"path": "b"}']
    renderer = ViewStreamRenderer(view=backend, state=state)
    await renderer.run_turn("edit")

    assert state.runtime.permission_responses == [
        ("edit", {"edited_args": {"path": "b"}}),
    ]


@pytest.mark.asyncio
async def test_permission_request_edit_invalid_json_warns_and_allows(
    backend: StubRecordingBackend,
) -> None:
    events = [
        StreamPermissionRequest(
            tool_name="write_file",
            args_preview="{}",
            diff_lines=(),
            pending_files=(),
        ),
    ]
    state = _make_state(events=events)
    backend.scripted_select = ["edit"]
    backend.scripted_text = ["not-json"]
    renderer = ViewStreamRenderer(view=backend, state=state)
    await renderer.run_turn("edit")

    assert state.runtime.permission_responses == [("allow", {})]
    assert any("Invalid JSON" in w for w in backend.warning_lines)


@pytest.mark.asyncio
async def test_permission_dialog_cancel_treated_as_deny(
    backend: StubRecordingBackend,
) -> None:
    events = [
        StreamPermissionRequest(
            tool_name="bash",
            args_preview="cmd=ls",
            diff_lines=(),
            pending_files=(),
        ),
    ]
    state = _make_state(events=events)

    # Override show_select to raise DialogCancelled once.
    async def cancelling_select(prompt, choices, default=None):
        raise DialogCancelled()

    backend.show_select = cancelling_select  # type: ignore[method-assign]

    renderer = ViewStreamRenderer(view=backend, state=state)
    await renderer.run_turn("ls")

    assert state.runtime.permission_responses == [("deny", {})]


# === Compaction ===


@pytest.mark.asyncio
async def test_compaction_start_and_done_prints_and_fires_hook(
    backend: StubRecordingBackend,
) -> None:
    events = [
        StreamCompactionStart(used_tokens=16000, max_tokens=20000),
        StreamCompactionDone(before_messages=120, after_messages=40),
    ]
    state = _make_state(events=events)
    renderer = ViewStreamRenderer(view=backend, state=state)
    await renderer.run_turn("q")

    info_blob = "\n".join(backend.info_lines)
    assert "auto-compacting" in info_blob
    assert "16000/20000" in info_blob
    assert "120" in info_blob and "40" in info_blob
    # on_session_compaction should fire with removed count
    assert backend.session_compactions == [80]


# === Message stop ===


@pytest.mark.asyncio
async def test_message_stop_updates_token_counters_and_cost(
    backend: StubRecordingBackend,
) -> None:
    events = [
        StreamTextDelta(text="Done."),
        StreamMessageStop(
            usage=TokenUsage(
                input_tokens=100,
                output_tokens=20,
                cache_read_tokens=5,
                cache_creation_tokens=3,
            ),
            stop_reason="end_turn",
        ),
    ]
    state = _make_state(events=events)
    renderer = ViewStreamRenderer(view=backend, state=state)
    await renderer.run_turn("q")

    assert state.input_tokens == 100
    assert state.output_tokens == 20
    assert state.last_stop_reason == "end_turn"
    assert state.cost_tracker.usages == [
        dict(
            input_tokens=100,
            output_tokens=20,
            cache_read_tokens=5,
            cache_creation_tokens=3,
        ),
    ]

    # A status update should carry the cost and context_used fields.
    pushed = [
        s for s in backend.status_updates
        if s.cost_usd is not None or s.context_used_tokens is not None
    ]
    assert pushed
    last = pushed[-1]
    assert last.context_used_tokens == 100


@pytest.mark.asyncio
async def test_two_message_stops_accumulate(
    backend: StubRecordingBackend,
) -> None:
    """Multi-step turns emit StreamMessageStop multiple times as the
    agent steps through tool calls. Counters must accumulate."""
    events = [
        StreamMessageStop(
            usage=TokenUsage(input_tokens=10, output_tokens=5),
            stop_reason="end_turn",
        ),
        StreamMessageStop(
            usage=TokenUsage(input_tokens=20, output_tokens=8),
            stop_reason="end_turn",
        ),
    ]
    state = _make_state(events=events)
    renderer = ViewStreamRenderer(view=backend, state=state)
    await renderer.run_turn("q")

    assert state.input_tokens == 30
    assert state.output_tokens == 13


# === Error paths ===


@pytest.mark.asyncio
async def test_exception_mid_turn_prints_error_and_clears_streaming(
    backend: StubRecordingBackend,
) -> None:
    events = [RuntimeError("provider died")]
    state = _make_state(events=events)
    renderer = ViewStreamRenderer(view=backend, state=state)
    await renderer.run_turn("q")

    assert any("provider died" in e for e in backend.error_lines)
    # Streaming flag must be cleared even on exception
    assert backend.status_updates[-1].is_streaming is False
    # on_turn_end fires in the finally block
    assert backend.turn_ends == 1


# === Skill router ===


@pytest.mark.asyncio
async def test_skill_router_matches_print_info(
    backend: StubRecordingBackend,
) -> None:
    class FakeRouter:
        async def route_async(self, text: str):
            return [SimpleNamespace(name="python-patterns")]

    state = _make_state(events=[], skill_router=FakeRouter())
    renderer = ViewStreamRenderer(view=backend, state=state)
    await renderer.run_turn("help me")

    assert any("python-patterns" in line for line in backend.info_lines)


@pytest.mark.asyncio
async def test_skill_router_empty_match_is_silent(
    backend: StubRecordingBackend,
) -> None:
    class FakeRouter:
        async def route_async(self, text: str):
            return []

    state = _make_state(events=[], skill_router=FakeRouter())
    renderer = ViewStreamRenderer(view=backend, state=state)
    await renderer.run_turn("help me")

    # No [skills: ...] info line
    assert not any(line.startswith("[skills:") for line in backend.info_lines)


# === Turn summary ===


@pytest.mark.asyncio
async def test_turn_summary_info_line_at_end(
    backend: StubRecordingBackend,
) -> None:
    events = [
        StreamTextDelta(text="ok"),
        StreamMessageStop(
            usage=TokenUsage(input_tokens=10, output_tokens=5),
            stop_reason="end_turn",
        ),
    ]
    state = _make_state(events=events)
    renderer = ViewStreamRenderer(view=backend, state=state)
    await renderer.run_turn("q")

    # The last info line should be the turn summary
    summary = backend.info_lines[-1]
    assert summary.startswith("turn:")
    assert "in=10" in summary
    assert "out=5" in summary


# === Empty-response fallback with no thinking ===


@pytest.mark.asyncio
async def test_empty_response_with_output_tokens_prints_warning(
    backend: StubRecordingBackend,
) -> None:
    """When the model reported output tokens but nothing visible
    landed (e.g. all tokens went into a stripped tool_call), the
    renderer surfaces the diagnostic helper as a warning."""
    events = [
        StreamMessageStop(
            usage=TokenUsage(input_tokens=10, output_tokens=42),
            stop_reason="end_turn",
        ),
    ]
    state = _make_state(events=events)
    renderer = ViewStreamRenderer(view=backend, state=state)
    await renderer.run_turn("q")

    # Some warning line should have been printed by the helper
    assert backend.warning_lines, (
        f"expected empty-response warning, got: {backend.warning_lines}"
    )


# === run_turn delegates active_skill_content through ===


@pytest.mark.asyncio
async def test_active_skill_content_is_forwarded_to_runtime(
    backend: StubRecordingBackend,
) -> None:
    state = _make_state(events=[])
    renderer = ViewStreamRenderer(view=backend, state=state)
    await renderer.run_turn("q", active_skill_content="<skill>go for it</skill>")

    assert state.runtime.run_turn_calls == [
        dict(
            user_input="q",
            images=None,
            active_skill_content="<skill>go for it</skill>",
        ),
    ]
