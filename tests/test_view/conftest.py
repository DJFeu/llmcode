"""Shared fixtures for test_view/ — REPLPilot is the primary one.

REPLPilot is the test abstraction that replaces Textual's pilot_app
for the v2.0.0 REPL rewrite. It wraps a headless REPLBackend + its
dispatcher with an input injection channel and output capture, giving
tests a uniform surface like:

    async def test_some_behavior(repl_pilot):
        await repl_pilot.submit("/voice")
        assert repl_pilot.info_lines_contain("voice")

See spec section 9.3 for the rationale.
"""
from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Optional

import pytest
import pytest_asyncio

from llm_code.view.repl.backend import REPLBackend
from llm_code.view.types import MessageEvent, Role, StatusUpdate


class REPLPilot:
    """Test control surface for the REPL backend.

    Wraps a REPLBackend instance plus a fake dispatcher callback. The
    pilot is the exclusive way tests interact with the backend — don't
    poke at backend attributes directly unless you have a good reason.

    Usage:
        async def test_example(repl_pilot):
            await repl_pilot.submit("/version")
            assert any("version" in line for line in repl_pilot.info_lines)
    """

    def __init__(self, backend: REPLBackend) -> None:
        self.backend = backend
        self.submitted_inputs: list[str] = []
        self._handler: Optional[Callable[[str], Awaitable[None]]] = None

    async def start(self) -> None:
        """Initialize the backend (calls backend.start())."""
        await self.backend.start()

    async def stop(self) -> None:
        """Tear down the backend."""
        await self.backend.stop()

    def set_dispatcher(
        self,
        handler: Callable[[str], Awaitable[None]],
    ) -> None:
        """Install a dispatcher callback. Most tests use the default
        no-op or a small custom lambda."""
        self._handler = handler
        self.backend.set_input_handler(handler)

    # === Input injection ===

    async def submit(self, text: str) -> None:
        """Pretend the user typed `text` and pressed Enter.

        The installed dispatcher callback (if any) is awaited so the
        test can assert on post-turn state immediately after the call.
        """
        self.submitted_inputs.append(text)
        if self._handler is not None:
            await self._handler(text)

    async def pause(self, duration: float = 0.01) -> None:
        """Yield to the event loop for `duration` seconds."""
        await asyncio.sleep(duration)

    # === Output inspection ===

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
        """Fold all partial status updates into a merged snapshot."""
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

    # === Convenience query helpers ===

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

    # === Scripted dialog responses ===

    def script_confirms(self, *responses: bool) -> None:
        """Queue responses for subsequent show_confirm() calls."""
        self.backend.scripted_confirm.extend(responses)

    def script_selects(self, *responses: Any) -> None:
        self.backend.scripted_select.extend(responses)

    def script_texts(self, *responses: str) -> None:
        self.backend.scripted_text.extend(responses)

    def script_checklists(self, *responses: list) -> None:
        self.backend.scripted_checklist.extend(responses)

    def script_editor(self, *responses: str) -> None:
        self.backend.scripted_editor.extend(responses)


@pytest_asyncio.fixture
async def repl_pilot():
    """Async fixture yielding a fully-started REPLPilot.

    Uses the stub REPLBackend (M2) or real REPLBackend (M3+). Tests
    using this fixture don't need to know which — the Protocol surface
    is identical.

    Example:
        async def test_info_print(repl_pilot):
            repl_pilot.backend.print_info("hello")
            assert repl_pilot.info_lines == ["hello"]
    """
    backend = REPLBackend()
    pilot = REPLPilot(backend)
    await pilot.start()
    try:
        yield pilot
    finally:
        await pilot.stop()


@pytest_asyncio.fixture
async def repl_pilot_with_echo_dispatcher(repl_pilot):
    """REPLPilot pre-wired with a dispatcher that renders every input
    back as a Role.USER message. Useful for tests that want to assert
    on the full input -> echo -> status update flow without writing a
    custom dispatcher each time."""

    async def echo_dispatcher(text: str) -> None:
        repl_pilot.backend.on_turn_start()
        repl_pilot.backend.render_message(
            MessageEvent(role=Role.USER, content=text)
        )
        repl_pilot.backend.on_turn_end()

    repl_pilot.set_dispatcher(echo_dispatcher)
    return repl_pilot
