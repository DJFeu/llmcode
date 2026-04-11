# M2 — REPLPilot Test Abstraction

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `REPLPilot` — a pytest fixture that wraps a headless REPLBackend instance with its dispatcher and a Rich Console capture, exposing a clean assertion surface for component tests. This is the single most important leverage point for the progressive test transliteration strategy (C2 method, spec §9.2) — it replaces Textual's `pilot_app` fixture and makes the ~400 transliterable tests into mechanical rewrites.

**Architecture:** A fixture module at `tests/test_view/conftest.py` that provides `repl_pilot` as an async pytest fixture. The fixture instantiates a REPL backend with `create_pipe_input()` (prompt_toolkit test harness) for input injection, and a `Console(file=io.StringIO)` for output capture. A `_REPLPilot` helper class exposes properties like `status_line`, `input`, `captured_renders`, plus async methods `press`, `type`, `submit`, `pause`, `feed_streaming_response`.

**Tech Stack:** Python 3.10+, pytest, pytest-asyncio, prompt_toolkit test utilities, rich Console capture, `asyncio`.

**Spec reference:** `docs/superpowers/specs/2026-04-11-llm-code-repl-mode-design.md` §9.3 (REPLPilot surface).

**Dependencies:** M1 complete (ViewBackend base + types imported by the pilot). REPLBackend itself does NOT exist yet — M2 writes a stub that the pilot can instantiate, and M3 fleshes it out.

---

## File Structure

### New files

- `llm_code/view/repl/__init__.py` — package marker (empty)
- `llm_code/view/repl/backend.py` — **stub** REPLBackend with minimal implementation that satisfies the ABC but does nothing useful (~200 lines; M3 expands this)
- `tests/test_view/conftest.py` — pytest fixtures including `repl_pilot` (~300 lines)
- `tests/test_view/test_pilot.py` — meta-tests for the pilot itself (~200 lines)

### Modified files

None.

### Files NOT touched

- All existing production code stays untouched.

---

## Tasks

### Task 2.1: Scaffold llm_code/view/repl/ package

**Files:**
- Create: `llm_code/view/repl/__init__.py`

- [ ] **Step 1: Create the directory**

Run: `mkdir -p llm_code/view/repl && touch llm_code/view/repl/__init__.py`

- [ ] **Step 2: Verify import**

Run: `/Users/adamhong/miniconda3/bin/python3 -c "import llm_code.view.repl; print('OK')"`

Expected: `OK`.

- [ ] **Step 3: Commit**

```bash
git add llm_code/view/repl/__init__.py
git commit -m "feat(view): scaffold llm_code/view/repl/ package"
```

---

### Task 2.2: Write stub REPLBackend

**Files:**
- Create: `llm_code/view/repl/backend.py`

This is a **stub** — it satisfies the `ViewBackend` ABC so `REPLPilot` can instantiate it, but most methods record call args into a list for test introspection and do NOT render anything to stdout. M3 replaces this stub with the real coordinator-backed implementation.

- [ ] **Step 1: Write the stub**

Write `llm_code/view/repl/backend.py`:

```python
"""REPLBackend stub — temporary no-op implementation for M2 pilot tests.

Replaced in M3 with the real ScreenCoordinator-backed implementation.
Until then, this stub records every method call into self._recorded
so the pilot can assert on call patterns without needing a real
terminal.

DO NOT import this from production code. M3 rewrites the file, at
which point the stub's introspection attributes disappear.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, Optional, Sequence, TypeVar

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

    def __init__(self, role: Role, on_commit: Callable[["_StubStreamingHandle"], None]) -> None:
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
    assert on dispatcher → backend interaction patterns without a
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
            import asyncio
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
```

- [ ] **Step 2: Syntax check**

Run: `/Users/adamhong/miniconda3/bin/python3 -c "import ast; ast.parse(open('llm_code/view/repl/backend.py').read()); print('OK')"`

Expected: `OK`.

- [ ] **Step 3: Verify the stub satisfies the ABC**

Run:
```bash
/Users/adamhong/miniconda3/bin/python3 -c "
from llm_code.view.repl.backend import REPLBackend
b = REPLBackend()
print(f'type: {type(b).__name__}')
print(f'is ViewBackend: {hasattr(type(b), \"__abstractmethods__\") and not type(b).__abstractmethods__}')
print(f'recording attrs: {len([a for a in dir(b) if not a.startswith(\"_\")])} public attrs')
"
```

Expected: no exception (ABC abstract methods all implemented), reasonable attr count (30+).

- [ ] **Step 4: Commit**

```bash
git add llm_code/view/repl/backend.py
git commit -m "feat(view): stub REPLBackend for M2 pilot testing (replaced in M3)"
```

---

### Task 2.3: Write conftest.py with REPLPilot fixture

**Files:**
- Create: `tests/test_view/conftest.py`

- [ ] **Step 1: Write the conftest**

Write `tests/test_view/conftest.py`:

```python
"""Shared fixtures for test_view/ — REPLPilot is the primary one.

REPLPilot is the test abstraction that replaces Textual's pilot_app
for the v2.0.0 REPL rewrite. It wraps a headless REPLBackend + its
dispatcher with an input injection channel and output capture, giving
tests a uniform surface like:

    async def test_some_behavior(repl_pilot):
        await repl_pilot.submit("/voice")
        assert repl_pilot.info_lines_contain("voice")

See spec §9.3 for the rationale.
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
    on the full input → echo → status update flow without writing a
    custom dispatcher each time."""

    async def echo_dispatcher(text: str) -> None:
        repl_pilot.backend.on_turn_start()
        repl_pilot.backend.render_message(
            MessageEvent(role=Role.USER, content=text)
        )
        repl_pilot.backend.on_turn_end()

    repl_pilot.set_dispatcher(echo_dispatcher)
    return repl_pilot
```

- [ ] **Step 2: Syntax check**

Run: `/Users/adamhong/miniconda3/bin/python3 -c "import ast; ast.parse(open('tests/test_view/conftest.py').read()); print('OK')"`

Expected: `OK`.

- [ ] **Step 3: Commit**

```bash
git add tests/test_view/conftest.py
git commit -m "test(view): REPLPilot fixture for component test transliteration"
```

---

### Task 2.4: Write meta-tests for the pilot itself

**Files:**
- Create: `tests/test_view/test_pilot.py`

- [ ] **Step 1: Write pilot meta-tests**

Write `tests/test_view/test_pilot.py`:

```python
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
```

- [ ] **Step 2: Run the meta-tests**

Run: `/Users/adamhong/miniconda3/bin/python3 -m pytest tests/test_view/test_pilot.py -v`

Expected: all ~19 meta-tests pass.

- [ ] **Step 3: Check test count**

Run: `/Users/adamhong/miniconda3/bin/python3 -m pytest tests/test_view/test_pilot.py --collect-only -q 2>&1 | tail -3`

Expected: 19 tests collected.

- [ ] **Step 4: Commit**

```bash
git add tests/test_view/test_pilot.py
git commit -m "test(view): meta-tests for REPLPilot fixture contract"
```

---

### Task 2.5: Run the full view test suite to verify M1 + M2 integration

**Files:** none (verification)

- [ ] **Step 1: Run all tests under tests/test_view/**

Run: `/Users/adamhong/miniconda3/bin/python3 -m pytest tests/test_view/ -v --tb=short`

Expected:
- ~18 Protocol conformance tests pass
- ~7 ConformanceSuite tests skipped (no backend fixture yet for the suite)
- ~19 pilot meta-tests pass
- Total: ~44 tests, 0 failures

- [ ] **Step 2: Verify no slowdown vs existing suite**

Run: `/Users/adamhong/miniconda3/bin/python3 -m pytest tests/test_view/ -q 2>&1 | tail -3`

Expected: completes in < 5 seconds.

- [ ] **Step 3: Push the branch**

Run: `git push origin feat/repl-mode`

---

## Milestone completion criteria

M2 is considered complete when:

- ✅ `llm_code/view/repl/__init__.py` exists
- ✅ `llm_code/view/repl/backend.py` stub satisfies the ABC (no unimplemented abstractmethods)
- ✅ `tests/test_view/conftest.py` defines `repl_pilot` and `repl_pilot_with_echo_dispatcher` fixtures
- ✅ `tests/test_view/test_pilot.py` has ~19 passing meta-tests
- ✅ Running `pytest tests/test_view/` shows ~44 tests, 0 failures
- ✅ All commits pushed to `origin/feat/repl-mode`

## Estimated effort

- Task 2.1 (scaffold): 2 minutes
- Task 2.2 (stub backend): 45 minutes (biggest task — writing a full stub that mirrors every Protocol method)
- Task 2.3 (conftest fixture): 30 minutes
- Task 2.4 (pilot meta-tests): 30 minutes
- Task 2.5 (verification): 5 minutes

**Total: ~2 hours** for a single focused session.

## Why this milestone exists

M2 is the **leverage amplifier** for everything after it. Without `REPLPilot`, every component test in M3–M9 would need to figure out its own fake backend + introspection strategy, and every transliterated test from the old Textual suite would need a custom rewrite.

With `REPLPilot`:
- M3–M9 component tests get a uniform fixture
- Transliterating the ~400 salvageable Textual tests becomes a mechanical find-replace
- The conformance harness from M1 can later be subclassed with a real REPL backend fixture (M3) that tests backend↔coordinator integration
- Test data model stays consistent across milestones — adding a new assertion helper once in `REPLPilot` benefits every downstream test

The stub backend is intentionally a simple recorder (not a real coordinator-backed backend) because M2 ships BEFORE M3 (which writes the real coordinator). This decoupling is deliberate — it means M2 can be validated in isolation from the Rich Live + prompt_toolkit integration risks covered in M0 and M3.

## Next milestone

After M2 is complete and pushed, proceed to **M3 — ScreenCoordinator skeleton**
(plan file: `2026-04-11-llm-code-repl-m3-coordinator.md`).
