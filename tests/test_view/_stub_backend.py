"""Recording stub backend used by pure-logic tests.

Mirrors the M2 stub REPLBackend: every ViewBackend method records its
args into public attributes for test introspection. Lives under tests/
not production because M3 replaced the production REPLBackend with a
real coordinator-backed implementation.

Tests that want to assert on real terminal output should use the
``repl_pilot`` fixture (real REPLBackend + StringIO Console).
Tests that want to assert on logic flow in isolation should use
``stub_repl_pilot`` which uses this class.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional, Sequence, TypeVar

from llm_code.view.base import InputHandler, ViewBackend
from llm_code.view.dialog_types import Choice, TextValidator
from llm_code.view.types import (
    MessageEvent,
    Role,
    RiskLevel,
    StatusUpdate,
    StreamingMessageHandle,
    ToolEventHandle,
)

T = TypeVar("T")


class _StubStreamingHandle:
    def __init__(self, role: Role) -> None:
        self.role = role
        self.chunks: list[str] = []
        self.committed = False
        self.aborted = False

    def feed(self, chunk: str) -> None:
        if not (self.committed or self.aborted):
            self.chunks.append(chunk)

    def commit(self) -> None:
        if not (self.committed or self.aborted):
            self.committed = True

    def abort(self) -> None:
        if not (self.committed or self.aborted):
            self.aborted = True

    @property
    def is_active(self) -> bool:
        return not (self.committed or self.aborted)

    @property
    def buffer(self) -> str:
        return "".join(self.chunks)


class _StubToolEventHandle:
    def __init__(self, tool_name: str, args: Dict[str, Any]) -> None:
        self.tool_name = tool_name
        self.args = args
        self.stdout_lines: list[str] = []
        self.stderr_lines: list[str] = []
        self.diff_text = ""
        self.committed = False
        self.success: Optional[bool] = None
        self.summary: Optional[str] = None
        self.error: Optional[str] = None
        self.exit_code: Optional[int] = None

    def feed_stdout(self, line: str) -> None:
        self.stdout_lines.append(line)

    def feed_stderr(self, line: str) -> None:
        self.stderr_lines.append(line)

    def feed_diff(self, diff_text: str) -> None:
        self.diff_text = diff_text

    def commit_success(self, *, summary=None, metadata=None) -> None:
        if self.committed:
            return
        self.committed = True
        self.success = True
        self.summary = summary

    def commit_failure(self, *, error: str, exit_code: Optional[int] = None) -> None:
        if self.committed:
            return
        self.committed = True
        self.success = False
        self.error = error
        self.exit_code = exit_code

    @property
    def is_active(self) -> bool:
        return not self.committed


class StubRecordingBackend(ViewBackend):
    """Recording backend. Every method call stores into public attrs."""

    def __init__(self) -> None:
        self._input_handler: Optional[InputHandler] = None
        self._running = False

        self.rendered_messages: list[MessageEvent] = []
        self.status_updates: list[StatusUpdate] = []
        self.streaming_handles: list[_StubStreamingHandle] = []
        self.tool_event_handles: list[_StubToolEventHandle] = []
        self.dialog_calls: list[tuple[str, dict]] = []
        self.info_lines: list[str] = []
        self.warning_lines: list[str] = []
        self.error_lines: list[str] = []
        self.panels: list[tuple[str, Optional[str]]] = []
        self.voice_events: list[tuple[str, dict]] = []
        self.turn_starts = 0
        self.turn_ends = 0
        self.session_compactions: list[int] = []
        self.session_loads: list[tuple[str, int]] = []
        self.fatal_errors: list[tuple[str, str, bool]] = []
        self.clears = 0

        self.scripted_confirm: list[bool] = []
        self.scripted_select: list[Any] = []
        self.scripted_text: list[str] = []
        self.scripted_checklist: list[list[Any]] = []
        self.scripted_editor: list[str] = []

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def run(self) -> None:
        while self._running:
            await asyncio.sleep(0.01)

    def mark_fatal_error(self, code: str, message: str, retryable: bool = True) -> None:
        self.fatal_errors.append((code, message, retryable))

    def request_exit(self) -> None:
        """Stops the stub's fake run() loop. Safe to call multiple times."""
        self._running = False

    def set_input_handler(self, handler: InputHandler) -> None:
        self._input_handler = handler

    def render_message(self, event: MessageEvent) -> None:
        self.rendered_messages.append(event)

    def start_streaming_message(
        self, role: Role, metadata: Optional[Dict[str, Any]] = None,
    ) -> StreamingMessageHandle:
        h = _StubStreamingHandle(role=role)
        self.streaming_handles.append(h)
        return h

    def start_tool_event(
        self, tool_name: str, args: Dict[str, Any],
    ) -> ToolEventHandle:
        h = _StubToolEventHandle(tool_name=tool_name, args=args)
        self.tool_event_handles.append(h)
        return h

    def update_status(self, status: StatusUpdate) -> None:
        self.status_updates.append(status)

    async def show_confirm(
        self, prompt: str, default: bool = False, risk: RiskLevel = RiskLevel.NORMAL,
    ) -> bool:
        self.dialog_calls.append(("confirm", {"prompt": prompt, "default": default, "risk": risk}))
        if self.scripted_confirm:
            return self.scripted_confirm.pop(0)
        return default

    async def show_select(
        self, prompt: str, choices: Sequence[Choice[T]], default: Optional[T] = None,
    ) -> T:
        self.dialog_calls.append(("select", {"prompt": prompt, "choices": list(choices), "default": default}))
        if self.scripted_select:
            return self.scripted_select.pop(0)
        if default is not None:
            return default
        return choices[0].value

    async def show_text_input(
        self, prompt: str, default: Optional[str] = None,
        validator: Optional[TextValidator] = None, secret: bool = False,
    ) -> str:
        self.dialog_calls.append(("text", {"prompt": prompt, "default": default, "secret": secret}))
        if self.scripted_text:
            return self.scripted_text.pop(0)
        return default or ""

    async def show_checklist(
        self, prompt: str, choices: Sequence[Choice[T]],
        defaults: Optional[Sequence[T]] = None,
    ) -> Sequence[T]:
        self.dialog_calls.append(("checklist", {"prompt": prompt, "choices": list(choices), "defaults": defaults}))
        if self.scripted_checklist:
            return self.scripted_checklist.pop(0)
        return list(defaults) if defaults else []

    def voice_started(self) -> None:
        self.voice_events.append(("started", {}))

    def voice_progress(self, seconds: float, peak: float) -> None:
        self.voice_events.append(("progress", {"seconds": seconds, "peak": peak}))

    def voice_stopped(self, reason: str) -> None:
        self.voice_events.append(("stopped", {"reason": reason}))

    def print_info(self, text: str) -> None:
        self.info_lines.append(text)

    def print_warning(self, text: str) -> None:
        self.warning_lines.append(text)

    def print_error(self, text: str) -> None:
        self.error_lines.append(text)

    def print_panel(self, content: str, title: Optional[str] = None) -> None:
        self.panels.append((content, title))

    def clear_screen(self) -> None:
        self.clears += 1

    def on_turn_start(self) -> None:
        self.turn_starts += 1

    def on_turn_end(self) -> None:
        self.turn_ends += 1

    def on_session_compaction(self, removed_tokens: int) -> None:
        self.session_compactions.append(removed_tokens)

    def on_session_load(self, session_id: str, message_count: int) -> None:
        self.session_loads.append((session_id, message_count))

    async def open_external_editor(
        self, initial_text: str = "", filename_hint: str = ".md",
    ) -> str:
        self.dialog_calls.append(("editor", {"initial_text": initial_text, "filename_hint": filename_hint}))
        if self.scripted_editor:
            return self.scripted_editor.pop(0)
        return initial_text
