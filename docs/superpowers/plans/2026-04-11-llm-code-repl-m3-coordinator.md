# M3 — ScreenCoordinator Skeleton

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the M2 stub REPLBackend with a real `ScreenCoordinator`-backed implementation that owns a `prompt_toolkit.Application(full_screen=False)` + a `rich.Console` for output. At the end of M3, running `llmcode` (still wired to the old dispatcher) should show an empty status line at the bottom + an empty input area, accept typing, and exit on Ctrl+D — a minimum viable REPL skeleton with no components yet.

**Architecture:** `ScreenCoordinator` is the single class that manages the PT Application lifecycle, layout construction, the asyncio.Lock that arbitrates between PT redraws and Rich prints, and the hooks that component modules (M4–M9) plug into. It exposes a small, stable surface to `REPLBackend` (which delegates every Protocol method to the coordinator).

**Tech Stack:** Python 3.10+, `prompt_toolkit>=3.0.47` (Application, Layout, HSplit, Window, BufferControl, FormattedTextControl, KeyBindings, Style), `rich` (Console), `asyncio`.

**Spec reference:** `docs/superpowers/specs/2026-04-11-llm-code-repl-mode-design.md` §6.1 (ScreenCoordinator), §6.2 (bottom layout), §6.5 (input handling — skeleton only, full keybindings in M4).

**Dependencies:** M2 complete. The `REPLPilot` fixture from M2 is rewritten to work with both the stub (which we're replacing) and the real coordinator-backed implementation. M0 PoC findings must be PASS or PARTIAL.

---

## File Structure

### New files

- `llm_code/view/repl/coordinator.py` — `ScreenCoordinator` class (~550 lines)

### Modified files

- `llm_code/view/repl/backend.py` — rewrite from stub to coordinator-delegating implementation (~400 lines, replaces the ~350-line stub)
- `tests/test_view/conftest.py` — add a `real_repl_pilot` fixture that instantiates the real backend with a piped input, alongside the existing `repl_pilot` fixture (which still uses the stub for pure logic tests)
- `tests/test_view/test_pilot.py` — add meta-tests for `real_repl_pilot`

### New test files

- `tests/test_view/test_coordinator.py` — unit tests for ScreenCoordinator in isolation (~400 lines, ~20 tests)

### Files NOT touched

- `llm_code/view/base.py`, `types.py`, `dialog_types.py` — Protocol is stable from M1
- All production code outside `llm_code/view/`
- `llm_code/tui/` — still running the old TUI; coordinator runs parallel, not swapped in yet

---

## Tasks

### Task 3.1: Write ScreenCoordinator

**Files:**
- Create: `llm_code/view/repl/coordinator.py`

- [ ] **Step 1: Write the coordinator class**

Write `llm_code/view/repl/coordinator.py`:

```python
"""ScreenCoordinator — single owner of prompt_toolkit Application + Rich Console.

The coordinator is the only class in the REPL backend that talks directly
to the terminal. Every other component (StatusLine in M5, InputArea in M4,
LiveResponseRegion in M6, ToolEventRegion in M7, DialogPopover in M8,
VoiceOverlay in M9) delegates its display work back through the coordinator.

This single-owner invariant is the architectural response to the v1.x TUI
bug class (see spec §1.1 and §10.1 R1). With one lock and one Application,
there's exactly one place where screen-corruption bugs can originate, and
exactly one place to fix them.

M3 ships the skeleton: lifecycle (start/stop/run), empty layout (1-line
reverse-video status placeholder + 3-line empty input area), Ctrl+D exit,
and terminal-native scrollback for everything above. M4–M9 plug real
components into the layout slots.
"""
from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Optional

from prompt_toolkit import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import HSplit, Layout, Window
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.styles import Style
from rich.console import Console

from llm_code.view.types import (
    MessageEvent,
    StatusUpdate,
)


InputCallback = Callable[[str], Awaitable[None]]


class ScreenCoordinator:
    """Owns the prompt_toolkit Application and rich.Console for a REPL session.

    Invariants:

    1. Exactly one Application exists per coordinator instance.
    2. Exactly one Console (writing to the real stdout, not a buffer) exists.
    3. All direct writes to stdout go through the console (no bare print()).
    4. PT Application.invalidate() and console.print() never run concurrently
       — the ``_screen_lock`` asyncio.Lock arbitrates.
    5. The coordinator's ``run()`` is the main event loop; ``stop()`` sets
       ``_exit_requested`` and lets run() return cleanly.

    Component plug-in points (used in M4–M9):

    - ``_status_text_fn`` — callable returning the status line string.
      M5 replaces the placeholder with a StatusLine component.
    - ``_input_buffer`` — the prompt_toolkit Buffer for user typing.
      M4 swaps the barebones Buffer for an InputArea component with
      multi-line, slash-popover, and keybinding integration.
    - ``_key_bindings`` — the PT KeyBindings. M4 adds Enter/Shift+Enter/
      Ctrl+G/history/etc. M3 only wires Ctrl+D → exit.
    - ``_extra_layout_rows`` — optional HSplit children inserted above
      the input area. M6/M7 use this for ConditionalContainer-wrapped
      voice overlays, rate limit warnings, etc.
    """

    def __init__(
        self,
        *,
        console: Optional[Console] = None,
    ) -> None:
        # Console is the real terminal by default. Tests override with
        # Console(file=io.StringIO(), force_terminal=True) for output
        # capture.
        self._console = console or Console()

        # asyncio primitives
        self._screen_lock = asyncio.Lock()
        self._exit_event = asyncio.Event()
        self._exit_requested = False

        # prompt_toolkit state — constructed in start()
        self._app: Optional[Application] = None
        self._input_buffer: Buffer = Buffer(multiline=True)
        self._key_bindings = KeyBindings()

        # Input callback installed by backend.set_input_handler()
        self._input_callback: Optional[InputCallback] = None

        # Status state — M5 expands this into StatusLine component
        self._current_status = StatusUpdate()

        # Pre-register Ctrl+D to exit. M4 adds Enter/Shift+Enter/etc.
        @self._key_bindings.add("c-d")
        def _exit(event: Any) -> None:
            # Ctrl+D: exit on empty input buffer, delete-char otherwise
            if not self._input_buffer.text:
                self.request_exit()
                event.app.exit()

        # Ctrl+C: clear input, or exit if already empty
        @self._key_bindings.add("c-c")
        def _interrupt(event: Any) -> None:
            if self._input_buffer.text:
                self._input_buffer.reset()
            else:
                self.request_exit()
                event.app.exit()

        # Enter: submit current buffer to the input callback
        @self._key_bindings.add("enter")
        def _submit(event: Any) -> None:
            text = self._input_buffer.text.strip()
            if not text:
                return
            self._input_buffer.reset()
            if self._input_callback is not None:
                asyncio.create_task(self._invoke_callback(text))
            event.app.invalidate()

    async def _invoke_callback(self, text: str) -> None:
        """Wrap input_callback invocation so exceptions don't kill the
        event loop. Errors are printed via print_error()."""
        try:
            assert self._input_callback is not None
            await self._input_callback(text)
        except Exception as exc:  # noqa: BLE001 — we want any exception
            self.print_error(f"input handler failed: {exc}")
        finally:
            if self._app is not None and self._app.is_running:
                self._app.invalidate()

    # === Lifecycle ===

    async def start(self) -> None:
        """Construct the PT Application. Idempotent — safe to call twice,
        subsequent calls are no-ops."""
        if self._app is not None:
            return

        layout = self._build_layout()
        style = self._build_style()

        self._app = Application(
            layout=layout,
            key_bindings=self._key_bindings,
            full_screen=False,     # KEY: no alt-screen, scrollback stays native
            mouse_support=False,   # KEY: no mouse capture, native selection works
            style=style,
        )

    async def stop(self) -> None:
        """Tear down the PT Application. Idempotent."""
        if self._app is not None and self._app.is_running:
            self._app.exit()
        self._app = None

    async def run(self) -> None:
        """Main event loop. Blocks until the user requests exit."""
        if self._app is None:
            await self.start()
        assert self._app is not None

        try:
            await self._app.run_async()
        except (EOFError, KeyboardInterrupt):
            pass

        self._exit_event.set()

    def request_exit(self) -> None:
        """Signal the main loop to exit at the next iteration."""
        self._exit_requested = True
        self._exit_event.set()

    @property
    def is_running(self) -> bool:
        return self._app is not None and self._app.is_running

    # === Input handler wiring ===

    def set_input_callback(self, callback: InputCallback) -> None:
        """Install the async handler invoked on each submitted input."""
        self._input_callback = callback

    # === Layout construction ===

    def _build_layout(self) -> Layout:
        """Build the bottom layout: placeholder status line + empty input area.

        Components in M4+ replace these placeholders via the coordinator's
        layout swap API (to be designed when M4 needs it). For now, the
        layout is static with placeholder content.
        """
        status_window = Window(
            FormattedTextControl(self._status_text),
            height=1,
            style="class:status",
        )
        input_window = Window(
            BufferControl(buffer=self._input_buffer),
            height=3,
            style="class:input",
        )
        return Layout(HSplit([status_window, input_window]))

    def _status_text(self) -> str:
        """Current status line as a plain string.

        M5 replaces this with a formatted-text function that renders
        model/cost/tokens inline. For M3, it's an empty placeholder
        to verify layout wiring works.
        """
        return " llmcode REPL — M3 skeleton "

    def _build_style(self) -> Style:
        return Style.from_dict({
            "status": "reverse",
            "input": "",
        })

    # === Output methods delegated by REPLBackend ===
    # Each must acquire _screen_lock before writing to the console, so
    # PT redraws and our writes don't interleave.

    async def acquire_screen(self):
        """Async context manager: acquire _screen_lock for safe stdout writes.

        Usage:
            async with self._coordinator.acquire_screen():
                self._coordinator._console.print("...")
        """
        return self._screen_lock

    def render_message_sync(self, event: MessageEvent) -> None:
        """Print a user-echo / system-note message to scrollback.

        Synchronous version — safe when called from the PT key-binding
        dispatcher (which doesn't yield to the event loop). For async
        contexts, prefer render_message_async.
        """
        from rich.text import Text
        prefix_map = {
            "user": "[bold green]>[/bold green] ",
            "assistant": "[bold cyan]<[/bold cyan] ",
            "system": "[dim]·[/dim] ",
            "tool": "[dim]▸[/dim] ",
        }
        prefix = prefix_map.get(event.role.value, "")
        self._console.print(f"{prefix}{event.content}")

    async def render_message_async(self, event: MessageEvent) -> None:
        async with self._screen_lock:
            self.render_message_sync(event)

    def print_info_sync(self, text: str) -> None:
        self._console.print(f"[blue]ℹ[/blue] {text}")

    def print_warning_sync(self, text: str) -> None:
        self._console.print(f"[yellow]⚠[/yellow] {text}")

    def print_error_sync(self, text: str) -> None:
        self._console.print(f"[red]✗[/red] {text}")

    def print_panel_sync(self, content: str, title: Optional[str] = None) -> None:
        from rich.panel import Panel
        self._console.print(Panel(content, title=title, border_style="cyan"))

    def clear_screen_sync(self) -> None:
        """Clear the terminal. Uses Rich's console.clear() which
        respects the console's file (no-op on captured consoles)."""
        self._console.clear()

    # === Status ===

    def update_status(self, status: StatusUpdate) -> None:
        """Merge a partial StatusUpdate into the current state.

        M5 expands this to actually refresh the status line; for M3 we
        just store it so tests can assert on the merged state.
        """
        for field_name in status.__dataclass_fields__:
            value = getattr(status, field_name)
            # Merge rule: None = unchanged; False = unchanged (default);
            # non-None non-False = overwrite
            if value is None:
                continue
            if field_name == "is_streaming" and value is False:
                # False on is_streaming is a meaningful clear, always apply
                pass
            setattr(self._current_status, field_name, value)

        if self._app is not None and self._app.is_running:
            self._app.invalidate()

    @property
    def current_status(self) -> StatusUpdate:
        return self._current_status
```

- [ ] **Step 2: Syntax check**

Run: `/Users/adamhong/miniconda3/bin/python3 -c "import ast; ast.parse(open('llm_code/view/repl/coordinator.py').read()); print('OK')"`

Expected: `OK`.

- [ ] **Step 3: Import check**

Run: `/Users/adamhong/miniconda3/bin/python3 -c "from llm_code.view.repl.coordinator import ScreenCoordinator; c = ScreenCoordinator(); print(f'status: {c.current_status.model}')"`

Expected: `status: None`.

- [ ] **Step 4: Commit**

```bash
git add llm_code/view/repl/coordinator.py
git commit -m "feat(view): ScreenCoordinator skeleton (PT Application + Rich Console owner)"
```

---

### Task 3.2: Rewrite REPLBackend to delegate to ScreenCoordinator

**Files:**
- Modify: `llm_code/view/repl/backend.py` (replace the M2 stub entirely)

- [ ] **Step 1: Replace the stub with the delegating implementation**

Overwrite `llm_code/view/repl/backend.py`:

```python
"""REPLBackend — v2.0.0 REPL implementation of ViewBackend.

Delegates all display work to ScreenCoordinator. The backend itself
is thin: it wires Protocol methods to coordinator methods, manages
handle objects for streaming/tool events, and holds config/runtime
references.

M3 ships the skeleton (coordinator + empty layout). M4–M9 add the
components (status, input, popover, live response, tool events,
dialogs, voice).
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, Optional, Sequence, TypeVar

from rich.console import Console

from llm_code.view.base import InputHandler, ViewBackend
from llm_code.view.dialog_types import Choice, DialogCancelled, TextValidator
from llm_code.view.repl.coordinator import ScreenCoordinator
from llm_code.view.types import (
    MessageEvent,
    Role,
    RiskLevel,
    StatusUpdate,
    StreamingMessageHandle,
    ToolEventHandle,
)

T = TypeVar("T")


class _NullStreamingHandle:
    """M3 placeholder. Real implementation in M6 (LiveResponseRegion).

    Feeds chunks into an internal buffer, commits by printing the
    buffered text as a plain Rich render. No Live region yet.
    """

    def __init__(self, coordinator: ScreenCoordinator, role: Role) -> None:
        self._coordinator = coordinator
        self._role = role
        self._buffer = ""
        self._committed = False
        self._aborted = False

    def feed(self, chunk: str) -> None:
        if self._committed or self._aborted:
            return
        self._buffer += chunk

    def commit(self) -> None:
        if self._committed or self._aborted:
            return
        self._committed = True
        from rich.markdown import Markdown
        self._coordinator._console.print(Markdown(self._buffer))

    def abort(self) -> None:
        if self._committed or self._aborted:
            return
        self._aborted = True

    @property
    def is_active(self) -> bool:
        return not (self._committed or self._aborted)


class _NullToolEventHandle:
    """M3 placeholder. Real implementation in M7 (ToolEventRegion)."""

    def __init__(
        self,
        coordinator: ScreenCoordinator,
        tool_name: str,
        args: Dict[str, Any],
    ) -> None:
        self._coordinator = coordinator
        self._tool_name = tool_name
        self._args = args
        self._committed = False

        # Print start line immediately
        self._coordinator._console.print(
            f"[dim]▶[/dim] {tool_name}"
        )

    def feed_stdout(self, line: str) -> None:
        pass

    def feed_stderr(self, line: str) -> None:
        pass

    def feed_diff(self, diff_text: str) -> None:
        pass

    def commit_success(
        self,
        *,
        summary: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        if self._committed:
            return
        self._committed = True
        summary_text = summary or "done"
        self._coordinator._console.print(
            f"[green]✓[/green] {self._tool_name} · {summary_text}"
        )

    def commit_failure(
        self,
        *,
        error: str,
        exit_code: Optional[int] = None,
    ) -> None:
        if self._committed:
            return
        self._committed = True
        exit_str = f" · exit {exit_code}" if exit_code is not None else ""
        self._coordinator._console.print(
            f"[red]✗[/red] {self._tool_name} · {error}{exit_str}"
        )

    @property
    def is_active(self) -> bool:
        return not self._committed


class REPLBackend(ViewBackend):
    """REPL ViewBackend — prompt_toolkit + Rich implementation.

    All display concerns delegate to ``self._coordinator``. The backend
    itself exists to implement the ViewBackend ABC and hold references
    to config/runtime for future use.

    M3 scope: coordinator skeleton, null-style handles for streaming
    and tool events. M6/M7 replace the null handles with real ones.
    """

    def __init__(
        self,
        *,
        config: Any = None,
        runtime: Any = None,
        console: Optional[Console] = None,
    ) -> None:
        self._config = config
        self._runtime = runtime
        self._coordinator = ScreenCoordinator(console=console)

    @property
    def coordinator(self) -> ScreenCoordinator:
        """Exposed for tests and component wiring. Production code
        outside view/repl/ should NOT use this — use Protocol methods."""
        return self._coordinator

    # === Lifecycle ===

    async def start(self) -> None:
        await self._coordinator.start()

    async def stop(self) -> None:
        await self._coordinator.stop()

    async def run(self) -> None:
        await self._coordinator.run()

    def mark_fatal_error(
        self,
        code: str,
        message: str,
        retryable: bool = True,
    ) -> None:
        self._coordinator.print_error_sync(
            f"[{code}] {message} (retryable={retryable})"
        )

    # === Input ===

    def set_input_handler(self, handler: InputHandler) -> None:
        self._coordinator.set_input_callback(handler)

    # === Messages ===

    def render_message(self, event: MessageEvent) -> None:
        self._coordinator.render_message_sync(event)

    def start_streaming_message(
        self,
        role: Role,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> StreamingMessageHandle:
        return _NullStreamingHandle(self._coordinator, role)

    def start_tool_event(
        self,
        tool_name: str,
        args: Dict[str, Any],
    ) -> ToolEventHandle:
        return _NullToolEventHandle(self._coordinator, tool_name, args)

    def update_status(self, status: StatusUpdate) -> None:
        self._coordinator.update_status(status)

    # === Dialogs (M3 placeholder: always return default; M8 replaces) ===

    async def show_confirm(
        self,
        prompt: str,
        default: bool = False,
        risk: RiskLevel = RiskLevel.NORMAL,
    ) -> bool:
        self._coordinator.print_info_sync(f"[confirm] {prompt} (auto: {default})")
        return default

    async def show_select(
        self,
        prompt: str,
        choices: Sequence[Choice[T]],
        default: Optional[T] = None,
    ) -> T:
        self._coordinator.print_info_sync(f"[select] {prompt}")
        if default is not None:
            return default
        if choices:
            return choices[0].value
        raise DialogCancelled("no choices available")

    async def show_text_input(
        self,
        prompt: str,
        default: Optional[str] = None,
        validator: Optional[TextValidator] = None,
        secret: bool = False,
    ) -> str:
        self._coordinator.print_info_sync(f"[text] {prompt}")
        return default or ""

    async def show_checklist(
        self,
        prompt: str,
        choices: Sequence[Choice[T]],
        defaults: Optional[Sequence[T]] = None,
    ) -> Sequence[T]:
        self._coordinator.print_info_sync(f"[checklist] {prompt}")
        return list(defaults) if defaults else []

    # === Convenience output ===

    def print_info(self, text: str) -> None:
        self._coordinator.print_info_sync(text)

    def print_warning(self, text: str) -> None:
        self._coordinator.print_warning_sync(text)

    def print_error(self, text: str) -> None:
        self._coordinator.print_error_sync(text)

    def print_panel(self, content: str, title: Optional[str] = None) -> None:
        self._coordinator.print_panel_sync(content, title)

    def clear_screen(self) -> None:
        self._coordinator.clear_screen_sync()

    # === External editor (M3 placeholder; real impl via $EDITOR in M9 or later) ===

    async def open_external_editor(
        self,
        initial_text: str = "",
        filename_hint: str = ".md",
    ) -> str:
        self._coordinator.print_info_sync(
            "[editor] external editor not implemented yet (M3 placeholder)"
        )
        return initial_text
```

- [ ] **Step 2: Syntax check**

Run: `/Users/adamhong/miniconda3/bin/python3 -c "import ast; ast.parse(open('llm_code/view/repl/backend.py').read()); print('OK')"`

Expected: `OK`.

- [ ] **Step 3: Verify the ABC is satisfied**

Run:
```bash
/Users/adamhong/miniconda3/bin/python3 -c "
from llm_code.view.repl.backend import REPLBackend
b = REPLBackend()
print(f'class: {type(b).__name__}')
print(f'abstract methods unimpl: {getattr(type(b), \"__abstractmethods__\", frozenset())}')
print(f'coordinator: {type(b.coordinator).__name__}')
"
```

Expected:
```
class: REPLBackend
abstract methods unimpl: frozenset()
coordinator: ScreenCoordinator
```

- [ ] **Step 4: Commit**

```bash
git add llm_code/view/repl/backend.py
git commit -m "feat(view): REPLBackend delegates to ScreenCoordinator (M3 skeleton)"
```

---

### Task 3.3: Update conftest.py to support both stub and real backend

**Files:**
- Modify: `tests/test_view/conftest.py`

M2's conftest.py assumed `REPLBackend` was the stub with recording attributes like `backend.rendered_messages`. Now that M3 replaces it with a real coordinator-backed backend, those attributes no longer exist. We need:

1. A `stub_repl_pilot` fixture that continues to use the old recording-style backend for pure-logic tests (M2's meta-tests need this).
2. A `repl_pilot` fixture that uses the real backend + a `Console(file=StringIO)` capture for component tests.

Since the M2 stub is gone (we overwrote the file), we need to recreate a recording stub for tests that want it. Move the recording stub to `tests/test_view/_stub_backend.py` — it's test infrastructure, not production.

- [ ] **Step 1: Create test-only stub backend**

Write `tests/test_view/_stub_backend.py`:

```python
"""Recording stub backend used by pure-logic tests.

Mirrors the M2 stub REPLBackend: every ViewBackend method records its
args into public attributes for test introspection. Lives under tests/
not production because M3 replaced the production REPLBackend with a
real coordinator-backed implementation.

Tests that want to assert on real terminal output should use the
``real_repl_pilot`` fixture (real REPLBackend + StringIO Console).
Tests that want to assert on logic flow in isolation should use
``stub_repl_pilot`` which uses this class.
"""
from __future__ import annotations

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
        import asyncio
        while self._running:
            await asyncio.sleep(0.01)

    def mark_fatal_error(self, code: str, message: str, retryable: bool = True) -> None:
        self.fatal_errors.append((code, message, retryable))

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
```

- [ ] **Step 2: Rewrite conftest.py to support both fixtures**

Overwrite `tests/test_view/conftest.py`:

```python
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

import pytest
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
```

- [ ] **Step 3: Update the M2 pilot meta-tests to use the stub fixture**

The M2 meta-tests at `tests/test_view/test_pilot.py` used `repl_pilot` assuming the stub behavior. Rename their fixture references to `stub_repl_pilot` and keep the tests as-is.

Run a find-and-replace in `tests/test_view/test_pilot.py`:

- Replace all occurrences of `repl_pilot` with `stub_repl_pilot`
- Replace `repl_pilot_with_echo_dispatcher` with `stub_repl_pilot_with_echo_dispatcher`

- [ ] **Step 4: Run the meta-tests**

Run: `/Users/adamhong/miniconda3/bin/python3 -m pytest tests/test_view/test_pilot.py -v`

Expected: all ~19 meta-tests still pass.

- [ ] **Step 5: Commit**

```bash
git add tests/test_view/_stub_backend.py tests/test_view/conftest.py tests/test_view/test_pilot.py
git commit -m "test(view): split pilot fixtures — stub for logic, real for components"
```

---

### Task 3.4: Write ScreenCoordinator unit tests

**Files:**
- Create: `tests/test_view/test_coordinator.py`

- [ ] **Step 1: Write the test file**

Write `tests/test_view/test_coordinator.py`:

```python
"""Unit tests for ScreenCoordinator.

These tests exercise the coordinator in isolation — not through the
REPLBackend wrapper. They use a StringIO Console to capture Rich
output without a real terminal.
"""
from __future__ import annotations

import asyncio
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


@pytest.mark.asyncio
async def test_acquire_screen_is_a_lock_manager():
    """acquire_screen returns the asyncio.Lock for use as async with."""
    coord, _ = _make_coordinator()
    lock = await coord.acquire_screen()
    assert isinstance(lock, asyncio.Lock)


def test_coordinator_has_console():
    """Coordinator exposes its Console for test inspection."""
    coord, capture = _make_coordinator()
    assert coord._console is not None
    # Writing via the console goes to our capture
    coord._console.print("direct write")
    assert "direct write" in capture.getvalue()
```

- [ ] **Step 2: Run the coordinator tests**

Run: `/Users/adamhong/miniconda3/bin/python3 -m pytest tests/test_view/test_coordinator.py -v`

Expected: all ~20 tests pass.

- [ ] **Step 3: Commit**

```bash
git add tests/test_view/test_coordinator.py
git commit -m "test(view): unit tests for ScreenCoordinator"
```

---

### Task 3.5: Add meta-tests for the real backend pilot

**Files:**
- Modify: `tests/test_view/test_pilot.py` (append tests)

- [ ] **Step 1: Append real_pilot meta-tests**

Add these tests to the end of `tests/test_view/test_pilot.py`:

```python
# === Real REPLBackend pilot meta-tests ===


@pytest.mark.asyncio
async def test_real_pilot_info_capture(repl_pilot):
    """print_info via the real backend writes to the captured Console."""
    repl_pilot.backend.print_info("hello from real")
    assert repl_pilot.captured_contains("hello from real")


@pytest.mark.asyncio
async def test_real_pilot_error_capture(repl_pilot):
    repl_pilot.backend.print_error("oops")
    assert repl_pilot.captured_contains("oops")


@pytest.mark.asyncio
async def test_real_pilot_panel_capture(repl_pilot):
    repl_pilot.backend.print_panel("body", title="T")
    assert repl_pilot.captured_contains("body")
    assert repl_pilot.captured_contains("T")


@pytest.mark.asyncio
async def test_real_pilot_render_message(repl_pilot):
    """render_message produces terminal output we can assert on."""
    repl_pilot.backend.render_message(
        MessageEvent(role=Role.USER, content="user says hi")
    )
    assert repl_pilot.captured_contains("user says hi")


@pytest.mark.asyncio
async def test_real_pilot_status_update_does_not_crash(repl_pilot):
    """update_status on the real backend merges without error."""
    repl_pilot.backend.update_status(StatusUpdate(model="test"))
    repl_pilot.backend.update_status(StatusUpdate(cost_usd=0.01))
    status = repl_pilot.coordinator.current_status
    assert status.model == "test"
    assert status.cost_usd == 0.01


@pytest.mark.asyncio
async def test_real_pilot_streaming_handle_feeds_and_commits(repl_pilot):
    """start_streaming_message returns a working handle."""
    handle = repl_pilot.backend.start_streaming_message(role=Role.ASSISTANT)
    handle.feed("hello ")
    handle.feed("world")
    assert handle.is_active
    handle.commit()
    assert not handle.is_active
    # After commit the buffered content is printed to the capture
    assert repl_pilot.captured_contains("hello world")


@pytest.mark.asyncio
async def test_real_pilot_tool_event_handle(repl_pilot):
    """start_tool_event prints start + commit lines to capture."""
    handle = repl_pilot.backend.start_tool_event(
        tool_name="read_file", args={"path": "foo.py"},
    )
    handle.commit_success(summary="47 lines")
    out = repl_pilot.captured_output
    assert "read_file" in out
    assert "47 lines" in out


@pytest.mark.asyncio
async def test_real_pilot_tool_event_failure(repl_pilot):
    handle = repl_pilot.backend.start_tool_event(
        tool_name="bash", args={"cmd": "false"},
    )
    handle.commit_failure(error="nonzero", exit_code=1)
    out = repl_pilot.captured_output
    assert "bash" in out
    assert "nonzero" in out
```

- [ ] **Step 2: Run all pilot tests**

Run: `/Users/adamhong/miniconda3/bin/python3 -m pytest tests/test_view/test_pilot.py -v`

Expected: all ~27 tests pass (19 from M2 + 8 new real_pilot tests).

- [ ] **Step 3: Commit**

```bash
git add tests/test_view/test_pilot.py
git commit -m "test(view): meta-tests for real REPLBackend pilot (captured Console)"
```

---

### Task 3.6: Full verification pass

**Files:** none (verification)

- [ ] **Step 1: Run all view tests**

Run: `/Users/adamhong/miniconda3/bin/python3 -m pytest tests/test_view/ -v`

Expected: 0 failures. Total ~65 tests (18 protocol + 19 stub pilot + 20 coordinator + 8 real pilot).

- [ ] **Step 2: Verify existing llmcode tests aren't broken**

Run: `/Users/adamhong/miniconda3/bin/python3 -m pytest tests/test_tui/ tests/test_tui/test_prompt_history_e2e.py -q --tb=no 2>&1 | tail -5`

Expected: same results as v1.23.1 (all green). The v2.0.0 rewrite does NOT touch `tests/test_tui/` until M11.

- [ ] **Step 3: Push the branch**

Run: `git push origin feat/repl-mode`

---

## Milestone completion criteria

M3 is considered complete when:

- ✅ `llm_code/view/repl/coordinator.py` exists with `ScreenCoordinator` class
- ✅ `llm_code/view/repl/backend.py` is the real delegating implementation (not the M2 stub)
- ✅ `REPLBackend()` can be instantiated without error; all 17 abstractmethods are implemented
- ✅ `tests/test_view/_stub_backend.py` is the relocated M2 stub, used by `stub_repl_pilot`
- ✅ `tests/test_view/conftest.py` defines both `stub_repl_pilot` and `repl_pilot` fixtures
- ✅ `tests/test_view/test_coordinator.py` has ~20 passing unit tests
- ✅ `tests/test_view/test_pilot.py` has ~27 passing tests (19 M2 + 8 new real-pilot)
- ✅ `pytest tests/test_view/` shows 0 failures, ~65 tests
- ✅ `pytest tests/test_tui/` still passes (unchanged)
- ✅ All commits pushed to `origin/feat/repl-mode`

## Estimated effort

- Task 3.1 (coordinator): 60 minutes (biggest file, ~550 lines)
- Task 3.2 (backend rewrite): 30 minutes
- Task 3.3 (conftest split): 40 minutes
- Task 3.4 (coordinator tests): 30 minutes
- Task 3.5 (pilot tests): 20 minutes
- Task 3.6 (verification): 10 minutes

**Total: ~3 hours** for a single focused session.

## Why this milestone exists

M3 proves the architecture before any component work begins. After M3:

- `REPLBackend()` instantiates a real Application + Console wired together
- Component modules (M4–M9) can plug in via a stable coordinator surface
- Tests can exercise the real backend with captured output, building confidence incrementally
- M0's PoC hypothesis (Rich Live + PT Application coexist) gets a second validation in a more realistic context (albeit without a Live region yet — that's M6)

If M3 hits unexpected issues with prompt_toolkit layout wiring or asyncio integration, it's still cheap to stop and reconsider — only ~1000 lines of plan code exists, and the Protocol (M1) and test infrastructure (M2) are fully reusable under any alternative architecture.

## Next milestone

After M3 is complete, proceed to **M4 — Input Area + Slash Popover**
(plan file: `2026-04-11-llm-code-repl-m4-input-popover.md`).
