"""Meta-tests: the REPLPilot fixture itself must work correctly.

These tests pin the pilot's contract so that future component tests
relying on it don't hit surprises. If a pilot meta-test fails, the
entire test_view/ suite is probably broken in the same way.
"""
from __future__ import annotations

import pytest

from llm_code.view.dialog_types import Choice
from llm_code.view.types import MessageEvent, Role, RiskLevel, StatusUpdate


@pytest.mark.asyncio
async def test_pilot_yields_started_backend(repl_pilot):
    """The pilot fixture yields a backend that has had start() called."""
    assert repl_pilot.backend._running is True


@pytest.mark.asyncio
async def test_pilot_info_line_capture(repl_pilot):
    """print_info on the backend is visible via pilot.info_lines."""
    repl_pilot.backend.print_info("hello world")
    assert repl_pilot.info_lines == ["hello world"]
    assert repl_pilot.info_lines_contain("hello")


@pytest.mark.asyncio
async def test_pilot_warning_and_error_capture(repl_pilot):
    """Warnings and errors are captured separately."""
    repl_pilot.backend.print_warning("be careful")
    repl_pilot.backend.print_error("boom")
    assert repl_pilot.warning_lines == ["be careful"]
    assert repl_pilot.error_lines == ["boom"]
    assert repl_pilot.warning_lines_contain("careful")
    assert repl_pilot.error_lines_contain("boom")


@pytest.mark.asyncio
async def test_pilot_panel_capture(repl_pilot):
    """print_panel captures content and title."""
    repl_pilot.backend.print_panel("body content", title="My Title")
    assert repl_pilot.panels == [("body content", "My Title")]


@pytest.mark.asyncio
async def test_pilot_rendered_messages_in_order(repl_pilot):
    """render_message calls append in order."""
    repl_pilot.backend.render_message(MessageEvent(role=Role.USER, content="first"))
    repl_pilot.backend.render_message(
        MessageEvent(role=Role.ASSISTANT, content="second")
    )
    roles = [m.role for m in repl_pilot.rendered_messages]
    assert roles == [Role.USER, Role.ASSISTANT]
    assert repl_pilot.last_rendered_message_role() == Role.ASSISTANT


@pytest.mark.asyncio
async def test_pilot_status_update_merge(repl_pilot):
    """Partial StatusUpdate calls merge correctly via current_status."""
    repl_pilot.backend.update_status(StatusUpdate(model="Q3.5-122B"))
    repl_pilot.backend.update_status(StatusUpdate(cost_usd=0.05))
    repl_pilot.backend.update_status(StatusUpdate(cost_usd=0.10))  # overwrite

    merged = repl_pilot.current_status
    assert merged.model == "Q3.5-122B"
    assert merged.cost_usd == 0.10  # latest wins
    # Other fields remain None
    assert merged.branch is None


@pytest.mark.asyncio
async def test_pilot_streaming_handle_feed_and_commit(repl_pilot):
    """start_streaming_message returns a handle that records chunks."""
    handle = repl_pilot.backend.start_streaming_message(role=Role.ASSISTANT)
    handle.feed("hello ")
    handle.feed("world")
    assert handle.is_active is True
    handle.commit()
    assert handle.is_active is False
    assert handle.buffer == "hello world"
    assert repl_pilot.last_streaming_buffer() == "hello world"


@pytest.mark.asyncio
async def test_pilot_streaming_handle_abort(repl_pilot):
    """Aborted streaming handle is inactive; buffer preserved for inspection."""
    handle = repl_pilot.backend.start_streaming_message(role=Role.ASSISTANT)
    handle.feed("partial")
    handle.abort()
    assert handle.is_active is False
    assert handle.buffer == "partial"
    assert handle.committed is False
    assert handle.aborted is True


@pytest.mark.asyncio
async def test_pilot_tool_event_commit_success(repl_pilot):
    """start_tool_event returns a handle that supports feed/commit."""
    handle = repl_pilot.backend.start_tool_event(
        tool_name="read_file",
        args={"path": "foo.py"},
    )
    handle.feed_stdout("line 1")
    handle.feed_stdout("line 2")
    handle.commit_success(summary="2 lines read")

    assert handle.committed is True
    assert handle.success is True
    assert handle.summary == "2 lines read"
    assert handle.stdout_lines == ["line 1", "line 2"]


@pytest.mark.asyncio
async def test_pilot_tool_event_commit_failure(repl_pilot):
    """commit_failure captures error details."""
    handle = repl_pilot.backend.start_tool_event(
        tool_name="bash",
        args={"cmd": "false"},
    )
    handle.feed_stderr("something broke")
    handle.commit_failure(error="nonzero exit", exit_code=1)

    assert handle.committed is True
    assert handle.success is False
    assert handle.error == "nonzero exit"
    assert handle.exit_code == 1


@pytest.mark.asyncio
async def test_pilot_scripted_confirm(repl_pilot):
    """script_confirms queues responses for show_confirm."""
    repl_pilot.script_confirms(True, False)

    r1 = await repl_pilot.backend.show_confirm("first?")
    r2 = await repl_pilot.backend.show_confirm("second?")

    assert r1 is True
    assert r2 is False
    # Both calls recorded
    assert len(repl_pilot.dialog_calls) == 2
    assert repl_pilot.dialog_calls[0][0] == "confirm"
    assert repl_pilot.dialog_calls[1][0] == "confirm"


@pytest.mark.asyncio
async def test_pilot_scripted_confirm_falls_back_to_default(repl_pilot):
    """If no scripted response is queued, show_confirm returns default."""
    result = await repl_pilot.backend.show_confirm("ok?", default=True)
    assert result is True

    result = await repl_pilot.backend.show_confirm("ok?", default=False)
    assert result is False


@pytest.mark.asyncio
async def test_pilot_scripted_select(repl_pilot):
    """script_selects queues responses for show_select."""
    repl_pilot.script_selects("b")

    result = await repl_pilot.backend.show_select(
        "pick",
        choices=[
            Choice(value="a", label="A"),
            Choice(value="b", label="B"),
        ],
    )
    assert result == "b"


@pytest.mark.asyncio
async def test_pilot_scripted_select_falls_back_to_first(repl_pilot):
    """With no scripted response and no default, show_select returns
    the first choice's value."""
    result = await repl_pilot.backend.show_select(
        "pick",
        choices=[
            Choice(value="a", label="A"),
            Choice(value="b", label="B"),
        ],
    )
    assert result == "a"


@pytest.mark.asyncio
async def test_pilot_scripted_text(repl_pilot):
    """script_texts queues responses for show_text_input."""
    repl_pilot.script_texts("user typed this")

    result = await repl_pilot.backend.show_text_input("enter name:")
    assert result == "user typed this"


@pytest.mark.asyncio
async def test_pilot_voice_event_capture(repl_pilot):
    """voice_started/progress/stopped are captured with args."""
    repl_pilot.backend.voice_started()
    repl_pilot.backend.voice_progress(seconds=1.5, peak=0.42)
    repl_pilot.backend.voice_stopped(reason="vad_auto_stop")

    events = repl_pilot.voice_events
    assert [e[0] for e in events] == ["started", "progress", "stopped"]
    assert events[1][1] == {"seconds": 1.5, "peak": 0.42}
    assert events[2][1] == {"reason": "vad_auto_stop"}


@pytest.mark.asyncio
async def test_pilot_turn_hooks(repl_pilot):
    """on_turn_start/end increment counters."""
    repl_pilot.backend.on_turn_start()
    repl_pilot.backend.on_turn_end()
    repl_pilot.backend.on_turn_start()
    assert repl_pilot.turn_starts == 2
    assert repl_pilot.turn_ends == 1


@pytest.mark.asyncio
async def test_pilot_submit_without_dispatcher(repl_pilot):
    """submit() without a dispatcher records but doesn't crash."""
    await repl_pilot.submit("no dispatcher")
    assert repl_pilot.submitted_inputs == ["no dispatcher"]


@pytest.mark.asyncio
async def test_pilot_submit_invokes_dispatcher(repl_pilot):
    """submit() calls the installed dispatcher handler."""
    received: list[str] = []

    async def capture(text: str) -> None:
        received.append(text)

    repl_pilot.set_dispatcher(capture)
    await repl_pilot.submit("hello")
    await repl_pilot.submit("world")

    assert received == ["hello", "world"]


@pytest.mark.asyncio
async def test_echo_dispatcher_fixture(repl_pilot_with_echo_dispatcher):
    """The echo dispatcher fixture wires a Role.USER render per submit."""
    pilot = repl_pilot_with_echo_dispatcher
    await pilot.submit("first")
    await pilot.submit("second")

    assert [m.content for m in pilot.rendered_messages] == ["first", "second"]
    assert all(m.role == Role.USER for m in pilot.rendered_messages)
    assert pilot.turn_starts == 2
    assert pilot.turn_ends == 2
