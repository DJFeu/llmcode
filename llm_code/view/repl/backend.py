"""REPLBackend stub — temporary no-op implementation for M2 pilot tests.

Replaced in M3 with the real ScreenCoordinator-backed implementation.
Until then, this stub records every method call into self._recorded
so the pilot can assert on call patterns without needing a real
terminal.

DO NOT import this from production code. M3 rewrites the file, at
which point the stub's introspection attributes disappear.
"""
from __future__ import annotations

import asyncio
from typing import Any, Callable, Dict, Optional, Sequence, TypeVar

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
    """Records feeds and commit state for test introspection."""

    def __init__(
        self,
        role: Role,
        on_commit: Callable[["_StubStreamingHandle"], None],
    ) -> None:
        self.role = role
        self.chunks: list[str] = []
        self.committed: bool = False
        self.aborted: bool = False
        self._on_commit = on_commit

    def feed(self, chunk: str) -> None:
        if self.committed or self.aborted:
            return
        self.chunks.append(chunk)

    def commit(self) -> None:
        if self.committed or self.aborted:
            return
        self.committed = True
        self._on_commit(self)

    def abort(self) -> None:
        if self.committed or self.aborted:
            return
        self.aborted = True

    @property
    def is_active(self) -> bool:
        return not (self.committed or self.aborted)

    @property
    def buffer(self) -> str:
        return "".join(self.chunks)


class _StubToolEventHandle:
    """Records tool event lifecycle for test introspection."""

    def __init__(
        self,
        tool_name: str,
        args: Dict[str, Any],
        on_commit: Callable[["_StubToolEventHandle"], None],
    ) -> None:
        self.tool_name = tool_name
        self.args = args
        self.stdout_lines: list[str] = []
        self.stderr_lines: list[str] = []
        self.diff_text: str = ""
        self.committed: bool = False
        self.success: Optional[bool] = None
        self.summary: Optional[str] = None
        self.error: Optional[str] = None
        self.exit_code: Optional[int] = None
        self._on_commit = on_commit

    def feed_stdout(self, line: str) -> None:
        self.stdout_lines.append(line)

    def feed_stderr(self, line: str) -> None:
        self.stderr_lines.append(line)

    def feed_diff(self, diff_text: str) -> None:
        self.diff_text = diff_text

    def commit_success(
        self,
        *,
        summary: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        if self.committed:
            return
        self.committed = True
        self.success = True
        self.summary = summary
        self._on_commit(self)

    def commit_failure(
        self,
        *,
        error: str,
        exit_code: Optional[int] = None,
    ) -> None:
        if self.committed:
            return
        self.committed = True
        self.success = False
        self.error = error
        self.exit_code = exit_code
        self._on_commit(self)

    @property
    def is_active(self) -> bool:
        return not self.committed


class REPLBackend(ViewBackend):
    """Stub implementation for M2 pilot testing.

    Records all method calls into public attributes so tests can
    assert on dispatcher -> backend interaction patterns without a
    real terminal. M3 replaces this with a ScreenCoordinator-backed
    implementation.
    """

    def __init__(
        self,
        *,
        config: Any = None,
        runtime: Any = None,
    ) -> None:
        self._config = config
        self._runtime = runtime
        self._input_handler: Optional[InputHandler] = None
        self._running = False

        # Test introspection state
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
        self.turn_starts: int = 0
        self.turn_ends: int = 0
        self.session_compactions: list[int] = []
        self.session_loads: list[tuple[str, int]] = []
        self.fatal_errors: list[tuple[str, str, bool]] = []

        # Scripted dialog responses (tests inject these)
        self.scripted_confirm: list[bool] = []
        self.scripted_select: list[Any] = []
        self.scripted_text: list[str] = []
        self.scripted_checklist: list[list[Any]] = []
        self.scripted_editor: list[str] = []

    # === Lifecycle ===

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def run(self) -> None:
        """Stub run() loop — drained externally by the pilot via
        ``await backend._input_handler(text)`` directly."""
        self._running = True
        while self._running:
            await asyncio.sleep(0.01)

    def mark_fatal_error(self, code: str, message: str, retryable: bool = True) -> None:
        self.fatal_errors.append((code, message, retryable))

    # === Input ===

    def set_input_handler(self, handler: InputHandler) -> None:
        self._input_handler = handler

    # === Messages ===

    def render_message(self, event: MessageEvent) -> None:
        self.rendered_messages.append(event)

    def start_streaming_message(
        self,
        role: Role,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> StreamingMessageHandle:
        handle = _StubStreamingHandle(role=role, on_commit=lambda h: None)
        self.streaming_handles.append(handle)
        return handle

    def start_tool_event(
        self,
        tool_name: str,
        args: Dict[str, Any],
    ) -> ToolEventHandle:
        handle = _StubToolEventHandle(
            tool_name=tool_name,
            args=args,
            on_commit=lambda h: None,
        )
        self.tool_event_handles.append(handle)
        return handle

    def update_status(self, status: StatusUpdate) -> None:
        self.status_updates.append(status)

    # === Dialogs ===

    async def show_confirm(
        self,
        prompt: str,
        default: bool = False,
        risk: RiskLevel = RiskLevel.NORMAL,
    ) -> bool:
        self.dialog_calls.append(("confirm", {
            "prompt": prompt, "default": default, "risk": risk,
        }))
        if self.scripted_confirm:
            return self.scripted_confirm.pop(0)
        return default

    async def show_select(
        self,
        prompt: str,
        choices: Sequence[Choice[T]],
        default: Optional[T] = None,
    ) -> T:
        self.dialog_calls.append(("select", {
            "prompt": prompt, "choices": list(choices), "default": default,
        }))
        if self.scripted_select:
            return self.scripted_select.pop(0)
        if default is not None:
            return default
        return choices[0].value

    async def show_text_input(
        self,
        prompt: str,
        default: Optional[str] = None,
        validator: Optional[TextValidator] = None,
        secret: bool = False,
    ) -> str:
        self.dialog_calls.append(("text", {
            "prompt": prompt, "default": default, "secret": secret,
        }))
        if self.scripted_text:
            return self.scripted_text.pop(0)
        return default or ""

    async def show_checklist(
        self,
        prompt: str,
        choices: Sequence[Choice[T]],
        defaults: Optional[Sequence[T]] = None,
    ) -> Sequence[T]:
        self.dialog_calls.append(("checklist", {
            "prompt": prompt, "choices": list(choices), "defaults": defaults,
        }))
        if self.scripted_checklist:
            return self.scripted_checklist.pop(0)
        return list(defaults) if defaults else []

    # === Voice ===

    def voice_started(self) -> None:
        self.voice_events.append(("started", {}))

    def voice_progress(self, seconds: float, peak: float) -> None:
        self.voice_events.append(("progress", {"seconds": seconds, "peak": peak}))

    def voice_stopped(self, reason: str) -> None:
        self.voice_events.append(("stopped", {"reason": reason}))

    # === Convenience output ===

    def print_info(self, text: str) -> None:
        self.info_lines.append(text)

    def print_warning(self, text: str) -> None:
        self.warning_lines.append(text)

    def print_error(self, text: str) -> None:
        self.error_lines.append(text)

    def print_panel(self, content: str, title: Optional[str] = None) -> None:
        self.panels.append((content, title))

    # === Session hooks ===

    def on_turn_start(self) -> None:
        self.turn_starts += 1

    def on_turn_end(self) -> None:
        self.turn_ends += 1

    def on_session_compaction(self, removed_tokens: int) -> None:
        self.session_compactions.append(removed_tokens)

    def on_session_load(self, session_id: str, message_count: int) -> None:
        self.session_loads.append((session_id, message_count))

    # === External editor ===

    async def open_external_editor(
        self,
        initial_text: str = "",
        filename_hint: str = ".md",
    ) -> str:
        self.dialog_calls.append(("editor", {
            "initial_text": initial_text, "filename_hint": filename_hint,
        }))
        if self.scripted_editor:
            return self.scripted_editor.pop(0)
        return initial_text
