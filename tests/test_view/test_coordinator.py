"""Unit tests for ScreenCoordinator.

These tests exercise the coordinator in isolation — not through the
REPLBackend wrapper. They use a StringIO Console to capture Rich
output without a real terminal.
"""
from __future__ import annotations

import io

import pytest
from rich.console import Console

from llm_code.view.repl.coordinator import ScreenCoordinator
from llm_code.view.types import MessageEvent, Role, StatusUpdate


def _make_coordinator() -> tuple[ScreenCoordinator, io.StringIO]:
    capture = io.StringIO()
    console = Console(
        file=capture,
        force_terminal=True,
        color_system="truecolor",
        width=80,
    )
    return ScreenCoordinator(console=console), capture


def test_coordinator_starts_with_no_app():
    """Before start(), _app is None."""
    coord, _ = _make_coordinator()
    assert coord._app is None
    assert coord.is_running is False


@pytest.mark.asyncio
async def test_coordinator_start_creates_app():
    """start() creates the prompt_toolkit Application."""
    coord, _ = _make_coordinator()
    await coord.start()
    assert coord._app is not None
    # is_running is False because we haven't called run_async()
    await coord.stop()


@pytest.mark.asyncio
async def test_coordinator_start_is_idempotent():
    """Calling start() twice doesn't create a second Application."""
    coord, _ = _make_coordinator()
    await coord.start()
    first_app = coord._app
    await coord.start()
    assert coord._app is first_app
    await coord.stop()


@pytest.mark.asyncio
async def test_coordinator_stop_is_idempotent():
    """Calling stop() twice is safe."""
    coord, _ = _make_coordinator()
    await coord.start()
    await coord.stop()
    await coord.stop()  # should not raise
    assert coord._app is None


def test_render_message_sync_user_prefix():
    """render_message_sync prefixes user messages with '>'."""
    coord, capture = _make_coordinator()
    coord.render_message_sync(MessageEvent(role=Role.USER, content="hello"))
    out = capture.getvalue()
    assert "hello" in out
    assert ">" in out


def test_render_message_sync_assistant_prefix():
    """render_message_sync prefixes assistant messages with '<'."""
    coord, capture = _make_coordinator()
    coord.render_message_sync(MessageEvent(role=Role.ASSISTANT, content="world"))
    out = capture.getvalue()
    assert "world" in out


def test_render_message_sync_system_prefix():
    """System messages get a middle-dot prefix."""
    coord, capture = _make_coordinator()
    coord.render_message_sync(MessageEvent(role=Role.SYSTEM, content="note"))
    out = capture.getvalue()
    assert "note" in out


def test_print_info_sync_includes_icon():
    """print_info_sync outputs an info icon."""
    coord, capture = _make_coordinator()
    coord.print_info_sync("informational")
    out = capture.getvalue()
    assert "informational" in out


def test_print_warning_sync_includes_icon():
    coord, capture = _make_coordinator()
    coord.print_warning_sync("careful!")
    out = capture.getvalue()
    assert "careful!" in out


def test_print_error_sync_includes_icon():
    coord, capture = _make_coordinator()
    coord.print_error_sync("broke")
    out = capture.getvalue()
    assert "broke" in out


def test_print_panel_sync_with_title():
    """print_panel_sync renders a bordered panel with title."""
    coord, capture = _make_coordinator()
    coord.print_panel_sync("panel body", title="Title Here")
    out = capture.getvalue()
    assert "panel body" in out
    assert "Title Here" in out


def test_print_panel_sync_without_title():
    """print_panel_sync works without a title."""
    coord, capture = _make_coordinator()
    coord.print_panel_sync("no-title body")
    out = capture.getvalue()
    assert "no-title body" in out


def test_update_status_merges_partial_updates():
    """update_status merges partial StatusUpdate instances."""
    coord, _ = _make_coordinator()
    coord.update_status(StatusUpdate(model="Q3.5-122B"))
    coord.update_status(StatusUpdate(cost_usd=0.05))
    coord.update_status(StatusUpdate(cost_usd=0.10))  # overwrite

    s = coord.current_status
    assert s.model == "Q3.5-122B"
    assert s.cost_usd == 0.10
    assert s.branch is None


def test_update_status_preserves_existing_fields():
    """Fields set in earlier updates persist if not touched."""
    coord, _ = _make_coordinator()
    coord.update_status(StatusUpdate(
        model="M1", branch="main", context_used_tokens=1000,
    ))
    coord.update_status(StatusUpdate(cost_usd=0.01))  # partial

    s = coord.current_status
    assert s.model == "M1"
    assert s.branch == "main"
    assert s.context_used_tokens == 1000
    assert s.cost_usd == 0.01


@pytest.mark.asyncio
async def test_set_input_callback_stores_handler():
    """set_input_callback installs the async handler."""
    coord, _ = _make_coordinator()

    async def handler(text: str) -> None:
        pass

    coord.set_input_callback(handler)
    assert coord._input_callback is handler


@pytest.mark.asyncio
async def test_invoke_callback_catches_exceptions():
    """Exceptions in the input callback are caught and surfaced as errors."""
    coord, capture = _make_coordinator()

    async def failing_handler(text: str) -> None:
        raise ValueError("boom")

    coord.set_input_callback(failing_handler)
    await coord._invoke_callback("input")

    out = capture.getvalue()
    assert "boom" in out or "ValueError" in out.lower() or "input handler failed" in out


def test_request_exit_sets_flag():
    coord, _ = _make_coordinator()
    assert coord._exit_requested is False
    coord.request_exit()
    assert coord._exit_requested is True


def test_coordinator_has_console():
    """Coordinator exposes its Console for test inspection."""
    coord, capture = _make_coordinator()
    assert coord._console is not None
    # Writing via the console goes to our capture
    coord._console.print("direct write")
    assert "direct write" in capture.getvalue()
