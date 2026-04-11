"""Shared fixtures for test_view/.

Two pilot flavors:

1. ``stub_repl_pilot`` — uses ``StubRecordingBackend`` for pure-logic
   tests that need to assert on call patterns without a real terminal.
2. ``repl_pilot`` — uses real ``REPLBackend`` with an in-memory
   ``Console(file=StringIO)`` for component tests that need to verify
   actual rendered output.

Most tests use ``repl_pilot``. ``stub_repl_pilot`` is reserved for
dispatcher/command tests where all we care about is "did the backend
get called with X".
"""
from __future__ import annotations

import asyncio
import io
from typing import Any, Awaitable, Callable, Optional

import pytest_asyncio
from rich.console import Console

from llm_code.view.repl.backend import REPLBackend
from llm_code.view.types import MessageEvent, Role, StatusUpdate
from tests.test_view._stub_backend import StubRecordingBackend


class StubREPLPilot:
    """Test control surface over StubRecordingBackend."""

    def __init__(self, backend: StubRecordingBackend) -> None:
        self.backend = backend
        self.submitted_inputs: list[str] = []
        self._handler: Optional[Callable[[str], Awaitable[None]]] = None

    async def start(self) -> None:
        await self.backend.start()

    async def stop(self) -> None:
        await self.backend.stop()

    def set_dispatcher(self, handler: Callable[[str], Awaitable[None]]) -> None:
        self._handler = handler
        self.backend.set_input_handler(handler)

    async def submit(self, text: str) -> None:
        self.submitted_inputs.append(text)
        if self._handler is not None:
            await self._handler(text)

    async def pause(self, duration: float = 0.01) -> None:
        await asyncio.sleep(duration)

    @property
    def rendered_messages(self) -> list[MessageEvent]:
        return list(self.backend.rendered_messages)

    @property
    def info_lines(self) -> list[str]:
        return list(self.backend.info_lines)

    @property
    def warning_lines(self) -> list[str]:
        return list(self.backend.warning_lines)

    @property
    def error_lines(self) -> list[str]:
        return list(self.backend.error_lines)

    @property
    def panels(self) -> list[tuple[str, Optional[str]]]:
        return list(self.backend.panels)

    @property
    def status_updates(self) -> list[StatusUpdate]:
        return list(self.backend.status_updates)

    @property
    def current_status(self) -> StatusUpdate:
        merged = StatusUpdate()
        for update in self.backend.status_updates:
            for field_name in update.__dataclass_fields__:
                value = getattr(update, field_name)
                if value is not None:
                    setattr(merged, field_name, value)
        return merged

    @property
    def streaming_handles(self):
        return list(self.backend.streaming_handles)

    @property
    def tool_event_handles(self):
        return list(self.backend.tool_event_handles)

    @property
    def dialog_calls(self) -> list[tuple[str, dict]]:
        return list(self.backend.dialog_calls)

    @property
    def voice_events(self) -> list[tuple[str, dict]]:
        return list(self.backend.voice_events)

    @property
    def turn_starts(self) -> int:
        return self.backend.turn_starts

    @property
    def turn_ends(self) -> int:
        return self.backend.turn_ends

    def info_lines_contain(self, substring: str) -> bool:
        return any(substring in line for line in self.info_lines)

    def warning_lines_contain(self, substring: str) -> bool:
        return any(substring in line for line in self.warning_lines)

    def error_lines_contain(self, substring: str) -> bool:
        return any(substring in line for line in self.error_lines)

    def last_rendered_message_role(self) -> Optional[Role]:
        if not self.rendered_messages:
            return None
        return self.rendered_messages[-1].role

    def last_streaming_buffer(self) -> Optional[str]:
        if not self.streaming_handles:
            return None
        return self.streaming_handles[-1].buffer

    def script_confirms(self, *responses: bool) -> None:
        self.backend.scripted_confirm.extend(responses)

    def script_selects(self, *responses: Any) -> None:
        self.backend.scripted_select.extend(responses)

    def script_texts(self, *responses: str) -> None:
        self.backend.scripted_text.extend(responses)

    def script_checklists(self, *responses: list) -> None:
        self.backend.scripted_checklist.extend(responses)

    def script_editor(self, *responses: str) -> None:
        self.backend.scripted_editor.extend(responses)


class RealREPLPilot:
    """Test control surface over real REPLBackend + StringIO Console.

    Used by component tests in M3+ that need to assert on actual
    rendered output. The backend's coordinator runs with an in-memory
    Console, so no terminal is required.
    """

    def __init__(self, backend: REPLBackend, capture: io.StringIO) -> None:
        self.backend = backend
        self._capture = capture

    async def start(self) -> None:
        await self.backend.start()

    async def stop(self) -> None:
        await self.backend.stop()

    @property
    def captured_output(self) -> str:
        return self._capture.getvalue()

    def captured_contains(self, substring: str) -> bool:
        return substring in self.captured_output

    def clear_capture(self) -> None:
        self._capture.seek(0)
        self._capture.truncate()

    @property
    def coordinator(self):
        return self.backend.coordinator

    # === Key-press simulation ===

    async def press(self, key_name: str) -> None:
        """Fire the binding registered for ``key_name`` as if the user
        had pressed that key.

        Resolves the key name through prompt_toolkit's own parser so we
        match the same Keys enum the coordinator registered. Single-key
        bindings only (no multi-key sequences). Missing binding raises
        ``AssertionError`` so tests fail fast on typos.
        """
        # Use private parser — the only way to match kb.bindings' key
        # normalization exactly.
        from prompt_toolkit.key_binding.key_bindings import _parse_key

        kb = self.backend.coordinator._key_bindings
        parsed = _parse_key(key_name)
        matches = kb.get_bindings_for_keys((parsed,))
        if not matches:
            raise AssertionError(
                f"no binding found for {key_name!r} "
                f"(parsed as {parsed!r})"
            )

        # Minimal fake KeyPressEvent. The real handlers we register only
        # call event.app.exit() and event.app.invalidate(), so this
        # mock surface is enough.
        class _FakeApp:
            def exit(self) -> None:
                pass

            def invalidate(self) -> None:
                pass

        class _FakeEvent:
            app = _FakeApp()

        event = _FakeEvent()
        # Call the most specific binding (last registered wins, same
        # semantics as prompt_toolkit's own key processor).
        matches[-1].handler(event)

    async def type_text(self, text: str) -> None:
        """Insert ``text`` into the input buffer at the cursor position."""
        self.backend.coordinator._input_area.buffer.insert_text(text)


@pytest_asyncio.fixture
async def stub_repl_pilot():
    """Fixture using the recording stub backend (for pure-logic tests)."""
    backend = StubRecordingBackend()
    pilot = StubREPLPilot(backend)
    await pilot.start()
    try:
        yield pilot
    finally:
        await pilot.stop()


@pytest_asyncio.fixture
async def repl_pilot():
    """Fixture using the real REPLBackend with a StringIO Console capture.

    The Console is configured with ``force_terminal=True`` so Rich
    emits ANSI codes as if writing to a real terminal, which is what
    most component tests want to assert on.
    """
    capture = io.StringIO()
    console = Console(
        file=capture,
        force_terminal=True,
        color_system="truecolor",
        width=80,
        record=False,
    )
    backend = REPLBackend(console=console)
    pilot = RealREPLPilot(backend, capture)
    await pilot.start()
    try:
        yield pilot
    finally:
        await pilot.stop()


@pytest_asyncio.fixture
async def stub_repl_pilot_with_echo_dispatcher(stub_repl_pilot):
    """Stub pilot pre-wired with an echo dispatcher (M2 compatibility)."""
    async def echo_dispatcher(text: str) -> None:
        stub_repl_pilot.backend.on_turn_start()
        stub_repl_pilot.backend.render_message(
            MessageEvent(role=Role.USER, content=text)
        )
        stub_repl_pilot.backend.on_turn_end()

    stub_repl_pilot.set_dispatcher(echo_dispatcher)
    return stub_repl_pilot
