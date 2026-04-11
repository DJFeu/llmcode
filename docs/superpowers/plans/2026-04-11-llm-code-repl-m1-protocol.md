# M1 — ViewBackend Protocol Base

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the `ViewBackend` ABC, its supporting data types (`MessageEvent`, `StatusUpdate`, `Role`, `RiskLevel`, `StreamingMessageHandle`, `ToolEventHandle`), the relocated dialog types, and a Protocol conformance test suite that any future backend (REPL, Telegram, Discord, Slack, Web) must pass.

**Architecture:** Pure abstract layer in `llm_code/view/`. No concrete backend implementation yet — just the contract + types + conformance harness. Relocates the existing dialog Protocol files from `tui/dialogs/` to `view/` (since they're already view-agnostic) so the new Protocol surface is gathered in one place.

**Tech Stack:** Python 3.10+, `abc`, `dataclasses`, `typing.Protocol`, `enum`, pytest.

**Spec reference:** `docs/superpowers/specs/2026-04-11-llm-code-repl-mode-design.md` §5.1 (abstract base), §5.2 (data types), §5.3 (dispatcher usage), §12.1 (file inventory).

**Dependencies:** M0 gate must have PASSED or PARTIAL. Do not start M1 while M0 is in doubt.

**Worktree:** From now onward, all v2.0.0 development happens on branch `feat/repl-mode`.

---

## File Structure

### New files

- `llm_code/view/__init__.py` — package marker (empty)
- `llm_code/view/base.py` — `ViewBackend` ABC (~280 lines)
- `llm_code/view/types.py` — `MessageEvent`, `StatusUpdate`, `Role`, `RiskLevel`, handle Protocols (~180 lines)
- `llm_code/view/dialog_types.py` — `Choice`, `TextValidator`, `DialogCancelled`, `DialogValidationError` (relocated from `tui/dialogs/api.py`) (~120 lines)
- `llm_code/view/ADDING_A_BACKEND.md` — contributor doc explaining how to add a new ViewBackend implementation (~200 lines)
- `tests/test_view/__init__.py` — package marker (empty)
- `tests/test_view/test_protocol_conformance.py` — abstract test class that every backend reuses (~350 lines)

### Modified files

- None. The existing `tui/dialogs/api.py` stays in place until M11 — M1 just creates a new copy at `view/dialog_types.py` and has the new `view/base.py` import from the new location. The old `tui/dialogs/api.py` keeps working for `tui/` code during the transition.

### Files NOT touched

- Everything under `llm_code/runtime/`, `llm_code/api/`, `llm_code/tools/`, `llm_code/memory/`, `llm_code/recovery/`.
- All of `llm_code/tui/` (will be deleted in M11).
- All of `llm_code/cli/`.

---

## Branch setup

### Task 1.0: Create feat/repl-mode branch

**Files:** none (git only)

- [ ] **Step 1: Verify you are on main at v1.23.1**

Run: `git branch --show-current && git describe --tags --exact-match HEAD 2>/dev/null || git log --oneline -1`

Expected: `main` and either `v1.23.1` or the commit `bf72f970` (or a later commit if M0 experiments landed).

- [ ] **Step 2: Create and switch to feat/repl-mode**

Run: `git checkout -b feat/repl-mode`

Expected: `Switched to a new branch 'feat/repl-mode'`.

- [ ] **Step 3: Push the branch to origin**

Run: `git push -u origin feat/repl-mode`

Expected: `Branch 'feat/repl-mode' set up to track remote branch 'feat/repl-mode' from 'origin'.`

---

## Tasks

### Task 1.1: Create view/ package marker

**Files:**
- Create: `llm_code/view/__init__.py`

- [ ] **Step 1: Create the directory and marker**

Run: `mkdir -p llm_code/view && touch llm_code/view/__init__.py`

- [ ] **Step 2: Verify the package imports**

Run: `/Users/adamhong/miniconda3/bin/python3 -c "import llm_code.view; print('view package importable')"`

Expected: `view package importable`.

- [ ] **Step 3: Commit**

```bash
git add llm_code/view/__init__.py
git commit -m "feat(view): scaffold llm_code/view/ package"
```

---

### Task 1.2: Write dialog_types.py (relocate + simplify)

**Files:**
- Create: `llm_code/view/dialog_types.py`
- Read: `llm_code/tui/dialogs/api.py` (for reference, do not modify)

- [ ] **Step 1: Read the source**

Read `llm_code/tui/dialogs/api.py` completely to understand the existing Protocol + data types.

- [ ] **Step 2: Write the new file**

Write `llm_code/view/dialog_types.py`:

```python
"""Dialog types shared across all ViewBackend implementations.

Relocated from tui/dialogs/api.py as part of the v2.0.0 view layer
reorganization. The old location remains in place until M11 when
tui/ is deleted; both files contain identical definitions during
the transition, kept in sync by hand.

These types are view-agnostic — the REPL backend, future Telegram/
Discord/Slack/Web backends, and the test scripted backend all
consume and produce Choice / TextValidator instances.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Generic, Optional, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class Choice(Generic[T]):
    """A single selectable option in a ``select`` or ``checklist``.

    ``value`` is what the caller receives when the user picks this
    choice. ``label`` is what the UI displays. ``hint`` is a secondary
    dim line shown beneath the label (optional). ``disabled`` prevents
    the choice from being picked — used for greying out unavailable
    options without hiding them entirely.
    """

    value: T
    label: str
    hint: Optional[str] = None
    disabled: bool = False


# A text validator returns None if the text is valid, or an error message
# string if it isn't. Used by show_text_input() to reject bad input
# with a user-visible error shown inline in the dialog.
TextValidator = Callable[[str], Optional[str]]


class DialogCancelled(Exception):
    """Raised by a backend when a dialog is cancelled (Esc, Ctrl+C,
    window closed, timeout, etc.). Callers should catch this and
    abort the operation that triggered the dialog.
    """


class DialogValidationError(Exception):
    """Raised by ``show_text_input`` when the validator rejects input
    and the backend cannot re-prompt inline. Most backends re-prompt
    rather than raising — this is a fallback for non-interactive
    backends (e.g., scripted test backends with exhausted responses).
    """

    def __init__(self, message: str, attempted_value: str) -> None:
        super().__init__(message)
        self.attempted_value = attempted_value
```

- [ ] **Step 3: Syntax check**

Run: `/Users/adamhong/miniconda3/bin/python3 -c "import ast; ast.parse(open('llm_code/view/dialog_types.py').read()); print('OK')"`

Expected: `OK`.

- [ ] **Step 4: Import check**

Run: `/Users/adamhong/miniconda3/bin/python3 -c "from llm_code.view.dialog_types import Choice, TextValidator, DialogCancelled; c = Choice(value=1, label='one'); print(c)"`

Expected: `Choice(value=1, label='one', hint=None, disabled=False)`.

- [ ] **Step 5: Commit**

```bash
git add llm_code/view/dialog_types.py
git commit -m "feat(view): relocate dialog types to llm_code/view/dialog_types.py"
```

---

### Task 1.3: Write types.py — core data types

**Files:**
- Create: `llm_code/view/types.py`

- [ ] **Step 1: Write types.py**

Write `llm_code/view/types.py`:

```python
"""Core view-layer data types shared across all backends.

These types are the 'wire format' between dispatcher/runtime and any
ViewBackend implementation. Keeping them immutable + explicit ensures
backends can't accidentally mutate shared state (which would leak
between backends in a hypothetical multi-backend gateway setup).

Based on hermes-agent's gateway/platforms/base.py MessageEvent /
SendResult pattern, simplified for view-only concerns (no platform-
specific metadata fields like thread_id, voice_message_id, etc.).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional, Protocol, runtime_checkable


class Role(Enum):
    """The speaker of a message."""
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


class RiskLevel(Enum):
    """Risk classification used by show_confirm() dialogs.

    Backends use this to color or visually distinguish confirmation
    prompts for destructive actions. A NORMAL-risk confirm might
    render as a quiet dim prompt; a CRITICAL-risk confirm must be
    loud and hard to dismiss accidentally.
    """
    NORMAL = "normal"       # read_file, ls, git status — informational
    ELEVATED = "elevated"   # edit_file, bash (read-only), write in cwd
    HIGH = "high"           # bash (mutating), write outside cwd, network
    CRITICAL = "critical"   # delete files, git push --force, rm -rf


@dataclass(frozen=True)
class MessageEvent:
    """A complete message that gets rendered to the view.

    Used for non-streaming messages: user input echo, system notes,
    compaction markers, tool result summaries for completed turns.

    Streaming assistant responses use StreamingMessageHandle instead
    (see below) because they need incremental updates.
    """
    role: Role
    content: str
    timestamp: datetime = field(default_factory=datetime.now)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class StatusUpdate:
    """Partial update to the backend's status display.

    Only non-None fields are applied; existing state persists for
    fields left as None. This lets the dispatcher update one field
    (e.g. just `cost_usd`) without having to re-state the entire
    status vector.

    Mutable on purpose — the dispatcher builds these incrementally
    during a turn and passes one to backend.update_status() at
    turn end (and optionally mid-turn for streaming token counts).
    """
    model: Optional[str] = None
    cwd: Optional[str] = None
    branch: Optional[str] = None
    permission_mode: Optional[str] = None
    context_used_tokens: Optional[int] = None
    context_limit_tokens: Optional[int] = None
    cost_usd: Optional[float] = None
    is_streaming: bool = False
    streaming_token_count: Optional[int] = None
    rate_limit_until: Optional[datetime] = None
    rate_limit_reqs_left: Optional[int] = None
    voice_active: bool = False
    voice_seconds: Optional[float] = None
    voice_peak: Optional[float] = None


@runtime_checkable
class StreamingMessageHandle(Protocol):
    """Handle to an in-progress streaming message region.

    Returned by ViewBackend.start_streaming_message(). The caller feeds
    chunks until the response is complete, then calls commit() to
    finalize the region. abort() discards the in-progress content
    (used on Ctrl+C cancellation or error).

    Runtime-checkable so tests can assert ``isinstance(handle, StreamingMessageHandle)``.
    """

    def feed(self, chunk: str) -> None:
        """Append a text chunk to the in-progress message."""
        ...

    def commit(self) -> None:
        """Finalize the message. After commit, feed() becomes a no-op."""
        ...

    def abort(self) -> None:
        """Discard the in-progress message without finalizing."""
        ...

    @property
    def is_active(self) -> bool:
        """True between start_streaming_message() and the first
        commit() / abort() call. False afterward."""
        ...


@runtime_checkable
class ToolEventHandle(Protocol):
    """Handle to an in-progress tool call display.

    REPL backend implements Style R (inline summary by default, diff
    tools and failures auto-expand). Other backends may implement
    different visual treatments but must honor the same feed/commit
    lifecycle.
    """

    def feed_stdout(self, line: str) -> None:
        """Append a stdout line from the running tool."""
        ...

    def feed_stderr(self, line: str) -> None:
        """Append a stderr line from the running tool."""
        ...

    def feed_diff(self, diff_text: str) -> None:
        """Attach a unified diff (for edit_file / write_file / apply_patch).

        Backends that auto-expand diffs (REPL style R) render this
        when commit_success() is called. Backends that don't just
        store it in metadata.
        """
        ...

    def commit_success(
        self,
        *,
        summary: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Finalize the tool call as successful."""
        ...

    def commit_failure(
        self,
        *,
        error: str,
        exit_code: Optional[int] = None,
    ) -> None:
        """Finalize the tool call as failed. Backends may visually
        distinguish failure (red border, expanded stderr, etc.)."""
        ...

    @property
    def is_active(self) -> bool:
        """True until commit_success or commit_failure is called."""
        ...
```

- [ ] **Step 2: Syntax check**

Run: `/Users/adamhong/miniconda3/bin/python3 -c "import ast; ast.parse(open('llm_code/view/types.py').read()); print('OK')"`

Expected: `OK`.

- [ ] **Step 3: Import check and smoke test**

Run:
```bash
/Users/adamhong/miniconda3/bin/python3 -c "
from llm_code.view.types import Role, RiskLevel, MessageEvent, StatusUpdate
from datetime import datetime
m = MessageEvent(role=Role.USER, content='hello')
print(f'role={m.role.value} content={m.content!r}')
s = StatusUpdate(model='Q3.5-122B', cost_usd=0.01)
print(f'model={s.model} cost=\${s.cost_usd}')
print(f'risk levels: {[r.value for r in RiskLevel]}')
"
```

Expected:
```
role=user content='hello'
model=Q3.5-122B cost=$0.01
risk levels: ['normal', 'elevated', 'high', 'critical']
```

- [ ] **Step 4: Commit**

```bash
git add llm_code/view/types.py
git commit -m "feat(view): add core data types (MessageEvent, StatusUpdate, Role, RiskLevel, handle Protocols)"
```

---

### Task 1.4: Write base.py — ViewBackend ABC

**Files:**
- Create: `llm_code/view/base.py`

- [ ] **Step 1: Write the ABC**

Write `llm_code/view/base.py`:

```python
"""ViewBackend — abstract base for all user-facing frontends.

First implementation: view.repl.backend.REPLBackend (v2.0.0).
Future: TelegramBackend, DiscordBackend, SlackBackend, WebBackend.

Design derived from Nous Research's hermes-agent BasePlatformAdapter
(gateway/platforms/base.py class BasePlatformAdapter). Simplified to
view-only concerns — no platform-specific fields like chat_id or
thread_id; session/chat context lives in the runtime layer, not the
view Protocol. Push-model input handling (set_input_handler) mirrors
hermes to unify REPL's pull-style loop with platform-side push-style
event handling.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import (
    Any,
    Awaitable,
    Callable,
    Dict,
    Optional,
    Sequence,
    TypeVar,
)

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

InputHandler = Callable[[str], Awaitable[None]]


class ViewBackend(ABC):
    """Protocol for all user-facing backends.

    Backends are responsible for:
    - Presenting messages, tool events, status updates, and dialogs
      to the user via whatever medium they target (terminal, chat
      platform, web UI, API events).
    - Receiving user input and delivering it to the dispatcher via
      the set_input_handler() callback (push model).
    - Managing their own lifecycle (start/stop/run) and screen/stream
      invariants.

    Backends are NOT responsible for:
    - Running the LLM conversation (that's the dispatcher + runtime).
    - Executing tools (that's tools/ + runtime).
    - Managing session state, cost tracking, or memory.
    - Interpreting user intent (slash commands, etc.) — the dispatcher
      receives raw user text and decides.

    Every ViewBackend subclass must implement all @abstractmethod
    methods. Default no-op implementations are provided for lifecycle
    hooks (on_turn_start, on_turn_end, on_session_*), voice notifications
    (voice_started/progress/stopped), and clear_screen — backends that
    don't have an equivalent concept can leave them alone.
    """

    # === Lifecycle ===

    @abstractmethod
    async def start(self) -> None:
        """Initialize the backend. Called once before run()."""

    @abstractmethod
    async def stop(self) -> None:
        """Tear down the backend. Called once after run() returns."""

    @abstractmethod
    async def run(self) -> None:
        """Main event loop. Returns when the user requests exit
        (REPL: Ctrl+D / /quit; Telegram: bot stopped; etc.)."""

    def mark_fatal_error(
        self,
        code: str,
        message: str,
        retryable: bool = True,
    ) -> None:
        """Notify the backend of an unrecoverable runtime error.

        Default: no-op. Backends that want to surface this to the user
        (e.g., print a red panel, flash a status, send an out-of-band
        notification) override.
        """
        pass

    # === Input (push model) ===

    @abstractmethod
    def set_input_handler(self, handler: InputHandler) -> None:
        """Install the async callback invoked on each submitted input.

        The backend's run() loop is responsible for reading/receiving
        user input (via prompt_toolkit in REPL, webhook in Telegram,
        etc.) and calling ``await handler(text)`` for each complete
        submission.

        The dispatcher registers its run_turn method as the handler,
        typically via ``backend.set_input_handler(dispatcher.run_turn)``.
        """

    # === Output: messages ===

    @abstractmethod
    def render_message(self, event: MessageEvent) -> None:
        """Render a complete (non-streaming) message.

        Used for: user input echo, system notes, compaction markers,
        summary lines. Streaming assistant responses go through
        start_streaming_message() instead.
        """

    @abstractmethod
    def start_streaming_message(
        self,
        role: Role,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> StreamingMessageHandle:
        """Begin a streaming message region.

        REPL implements via Strategy Z (Rich Live region + commit to
        scrollback). Returns a handle the caller uses to feed chunks
        and finalize.
        """

    # === Output: tool events ===

    @abstractmethod
    def start_tool_event(
        self,
        tool_name: str,
        args: Dict[str, Any],
    ) -> ToolEventHandle:
        """Begin a tool call display.

        REPL implements Style R: inline summary by default; diff tools
        (edit_file/write_file/apply_patch) and failures auto-expand.
        """

    # === Output: status ===

    @abstractmethod
    def update_status(self, status: StatusUpdate) -> None:
        """Update the persistent status display.

        Partial update: only non-None fields in ``status`` apply.
        Existing field values persist across calls.
        """

    # === Dialogs ===

    @abstractmethod
    async def show_confirm(
        self,
        prompt: str,
        default: bool = False,
        risk: RiskLevel = RiskLevel.NORMAL,
    ) -> bool:
        """Ask the user to confirm or deny. Returns True on confirm.

        Backends MUST honor the risk level visually — NORMAL can be
        dim; CRITICAL must be loud and hard to accept accidentally.

        Raises DialogCancelled if the user cancels (Esc, window close,
        etc.) — callers should treat this as a 'deny' or propagate it
        to abort the higher-level operation.
        """

    @abstractmethod
    async def show_select(
        self,
        prompt: str,
        choices: Sequence[Choice[T]],
        default: Optional[T] = None,
    ) -> T:
        """Let the user pick one choice from a list. Returns the
        selected choice's ``value``.

        Raises DialogCancelled on user cancel.
        """

    @abstractmethod
    async def show_text_input(
        self,
        prompt: str,
        default: Optional[str] = None,
        validator: Optional[TextValidator] = None,
        secret: bool = False,
    ) -> str:
        """Prompt the user for free-form text. Returns the submitted text.

        If ``validator`` is provided, backends SHOULD re-prompt on
        invalid input, showing the validator's error message inline.
        Backends that cannot re-prompt may raise DialogValidationError.

        ``secret=True`` asks the backend to mask input (password-style).
        Backends without a masking capability must still accept and
        return the text, but may log a warning.

        Raises DialogCancelled on user cancel.
        """

    @abstractmethod
    async def show_checklist(
        self,
        prompt: str,
        choices: Sequence[Choice[T]],
        defaults: Optional[Sequence[T]] = None,
    ) -> Sequence[T]:
        """Let the user pick any number of choices (including zero).
        Returns the list of selected values.

        Raises DialogCancelled on user cancel. An empty selection (no
        choices picked) is NOT a cancel — it returns ``[]``.
        """

    # === Voice notifications ===
    # Called BY the AudioRecorder (or equivalent), not BY the dispatcher.
    # Default no-ops so non-CLI backends can ignore without implementing.

    def voice_started(self) -> None:
        """The recorder has started capturing audio."""
        pass

    def voice_progress(self, seconds: float, peak: float) -> None:
        """Periodic update during active recording.

        ``seconds`` is elapsed recording time; ``peak`` is the
        normalized peak amplitude (0.0–1.0) of the last chunk.
        """
        pass

    def voice_stopped(self, reason: str) -> None:
        """Recording ended.

        ``reason`` is one of 'vad_auto_stop', 'manual_stop',
        'no_speech_timeout', 'permission_denied', 'error:<detail>'.
        """
        pass

    # === Convenience output ===

    @abstractmethod
    def print_info(self, text: str) -> None:
        """Print an informational message. No special styling required."""

    @abstractmethod
    def print_warning(self, text: str) -> None:
        """Print a warning message. Backends should visually distinguish
        warnings from info (color, icon, etc.)."""

    @abstractmethod
    def print_error(self, text: str) -> None:
        """Print an error message. Backends MUST visually distinguish
        errors from info/warning."""

    @abstractmethod
    def print_panel(
        self,
        content: str,
        title: Optional[str] = None,
    ) -> None:
        """Print a panel (boxed content with optional title).

        REPL uses a Rich Panel. Text-only backends may approximate
        with leading/trailing rules. Bot backends may render as a
        blockquote or code block.
        """

    def clear_screen(self) -> None:
        """Clear the visible area. Default no-op.

        REPL clears the terminal (Ctrl+L / /clear). Bot backends
        typically can't clear — they leave this as a no-op.
        """
        pass

    # === Session events ===
    # Default no-ops. Backends can override to react (e.g., flush state,
    # show a spinner, log telemetry).

    def on_turn_start(self) -> None:
        """Called at the start of every dispatcher turn."""
        pass

    def on_turn_end(self) -> None:
        """Called at the end of every dispatcher turn (success or failure)."""
        pass

    def on_session_compaction(self, removed_tokens: int) -> None:
        """Called when the conversation history is compacted."""
        pass

    def on_session_load(self, session_id: str, message_count: int) -> None:
        """Called after a saved session is loaded."""
        pass

    # === External editor ===

    @abstractmethod
    async def open_external_editor(
        self,
        initial_text: str = "",
        filename_hint: str = ".md",
    ) -> str:
        """Open an external editor and return the edited content.

        REPL: spawns ``$EDITOR`` on a temp file (blocks PT app during
        edit, resumes afterward).
        Bot backends: show a 'send as file' prompt or long-form compose.
        Web backends: show a modal textarea.

        Raises DialogCancelled if the user cancels without saving.
        """
```

- [ ] **Step 2: Syntax check**

Run: `/Users/adamhong/miniconda3/bin/python3 -c "import ast; ast.parse(open('llm_code/view/base.py').read()); print('OK')"`

Expected: `OK`.

- [ ] **Step 3: Import check**

Run: `/Users/adamhong/miniconda3/bin/python3 -c "from llm_code.view.base import ViewBackend; print('abstract methods:', len(ViewBackend.__abstractmethods__))"`

Expected: `abstract methods: 15` (start, stop, run, set_input_handler, render_message, start_streaming_message, start_tool_event, update_status, show_confirm, show_select, show_text_input, show_checklist, print_info, print_warning, print_error, print_panel, open_external_editor = 17; actual count may differ by 1-2 if I miscounted; just verify it's >= 15).

- [ ] **Step 4: Verify ABC cannot be instantiated directly**

Run: `/Users/adamhong/miniconda3/bin/python3 -c "
from llm_code.view.base import ViewBackend
try:
    b = ViewBackend()
    print('ERROR: instantiation should have failed')
except TypeError as e:
    print(f'OK: {e}')
"`

Expected: `OK: Can't instantiate abstract class ViewBackend ...`

- [ ] **Step 5: Commit**

```bash
git add llm_code/view/base.py
git commit -m "feat(view): add ViewBackend abstract base class"
```

---

### Task 1.5: Write the Protocol conformance test harness

**Files:**
- Create: `tests/test_view/__init__.py`
- Create: `tests/test_view/test_protocol_conformance.py`

- [ ] **Step 1: Create tests/test_view/__init__.py**

Run: `mkdir -p tests/test_view && touch tests/test_view/__init__.py`

- [ ] **Step 2: Write the conformance test file**

Write `tests/test_view/test_protocol_conformance.py`:

```python
"""Protocol conformance tests for ViewBackend implementations.

This module provides:
1. ``ViewBackendConformanceSuite`` — an abstract pytest test class that
   every backend subclasses and inherits. Ensures every backend honors
   the Protocol contract uniformly.
2. Per-method contract tests — assert method signatures, abstractness,
   default behaviors, and type correctness at the Protocol level
   (without needing a concrete backend).

Concrete backends (REPLBackend in M3+, TelegramBackend in v2.1+, etc.)
will import ``ViewBackendConformanceSuite`` and provide a fixture that
yields an instance, then inherit all tests for free.

Example future usage:

    class TestREPLBackendConformance(ViewBackendConformanceSuite):
        @pytest.fixture
        async def backend(self, tmp_path):
            ...
            yield REPLBackend(...)
"""
from __future__ import annotations

import inspect
from typing import get_type_hints

import pytest

from llm_code.view.base import ViewBackend
from llm_code.view.dialog_types import (
    Choice,
    DialogCancelled,
    DialogValidationError,
    TextValidator,
)
from llm_code.view.types import (
    MessageEvent,
    Role,
    RiskLevel,
    StatusUpdate,
    StreamingMessageHandle,
    ToolEventHandle,
)


# === Protocol-level tests (no concrete backend required) ===


def test_view_backend_is_abstract():
    """ViewBackend cannot be instantiated directly."""
    with pytest.raises(TypeError, match="abstract"):
        ViewBackend()  # type: ignore[abstract]


def test_view_backend_has_expected_abstract_methods():
    """The ABC's abstractmethods set matches the spec §5.1 list."""
    expected = {
        "start",
        "stop",
        "run",
        "set_input_handler",
        "render_message",
        "start_streaming_message",
        "start_tool_event",
        "update_status",
        "show_confirm",
        "show_select",
        "show_text_input",
        "show_checklist",
        "print_info",
        "print_warning",
        "print_error",
        "print_panel",
        "open_external_editor",
    }
    actual = set(ViewBackend.__abstractmethods__)
    assert actual == expected, (
        f"Abstract method set drift. "
        f"Missing: {expected - actual}. Extra: {actual - expected}."
    )


def test_view_backend_has_default_noop_methods():
    """These methods have default (non-abstract) implementations:
    mark_fatal_error, voice_*, clear_screen, on_turn_*, on_session_*."""
    default_methods = {
        "mark_fatal_error",
        "voice_started",
        "voice_progress",
        "voice_stopped",
        "clear_screen",
        "on_turn_start",
        "on_turn_end",
        "on_session_compaction",
        "on_session_load",
    }
    for name in default_methods:
        assert name in dir(ViewBackend), f"{name} missing from ViewBackend"
        assert name not in ViewBackend.__abstractmethods__, (
            f"{name} should have a default impl, not be abstract"
        )


def test_show_confirm_signature():
    """show_confirm takes prompt, default, risk, returns bool."""
    sig = inspect.signature(ViewBackend.show_confirm)
    params = sig.parameters
    assert "prompt" in params
    assert "default" in params and params["default"].default is False
    assert "risk" in params and params["risk"].default == RiskLevel.NORMAL


def test_show_select_signature():
    """show_select takes prompt, choices, default; returns T."""
    sig = inspect.signature(ViewBackend.show_select)
    params = sig.parameters
    assert "prompt" in params
    assert "choices" in params
    assert "default" in params and params["default"].default is None


def test_show_text_input_signature():
    """show_text_input takes prompt, default, validator, secret."""
    sig = inspect.signature(ViewBackend.show_text_input)
    params = sig.parameters
    assert "prompt" in params
    assert "default" in params and params["default"].default is None
    assert "validator" in params and params["validator"].default is None
    assert "secret" in params and params["secret"].default is False


def test_start_streaming_message_signature():
    """start_streaming_message takes role, metadata; returns handle."""
    sig = inspect.signature(ViewBackend.start_streaming_message)
    params = sig.parameters
    assert "role" in params
    assert "metadata" in params and params["metadata"].default is None


def test_start_tool_event_signature():
    """start_tool_event takes tool_name, args; returns handle."""
    sig = inspect.signature(ViewBackend.start_tool_event)
    params = sig.parameters
    assert "tool_name" in params
    assert "args" in params


# === Data type tests ===


def test_role_enum_values():
    """Role enum has exactly 4 values."""
    assert {r.value for r in Role} == {"user", "assistant", "system", "tool"}


def test_risk_level_enum_values():
    """RiskLevel enum has exactly 4 values in ascending severity."""
    expected = ["normal", "elevated", "high", "critical"]
    actual = [r.value for r in RiskLevel]
    assert actual == expected


def test_message_event_frozen():
    """MessageEvent is frozen (immutable)."""
    m = MessageEvent(role=Role.USER, content="hi")
    with pytest.raises((AttributeError, TypeError)):
        m.content = "changed"  # type: ignore[misc]


def test_message_event_defaults():
    """MessageEvent has sensible defaults."""
    from datetime import datetime
    m = MessageEvent(role=Role.ASSISTANT, content="response")
    assert m.metadata == {}
    assert isinstance(m.timestamp, datetime)


def test_status_update_partial_defaults_to_none():
    """StatusUpdate fields default to None so partial updates work."""
    s = StatusUpdate()
    assert s.model is None
    assert s.cost_usd is None
    assert s.is_streaming is False  # the one non-None default
    assert s.voice_active is False


def test_choice_frozen():
    """Choice is frozen."""
    c = Choice(value=1, label="one")
    with pytest.raises((AttributeError, TypeError)):
        c.label = "changed"  # type: ignore[misc]


def test_dialog_cancelled_is_exception():
    """DialogCancelled inherits from Exception."""
    assert issubclass(DialogCancelled, Exception)


def test_dialog_validation_error_carries_attempted_value():
    """DialogValidationError retains the rejected input."""
    err = DialogValidationError("bad email", attempted_value="nope")
    assert str(err) == "bad email"
    assert err.attempted_value == "nope"


# === Protocol runtime-check tests ===


def test_streaming_message_handle_is_runtime_checkable():
    """StreamingMessageHandle is a runtime_checkable Protocol so
    isinstance() works on duck-typed handles."""

    class FakeHandle:
        def feed(self, chunk: str) -> None: ...
        def commit(self) -> None: ...
        def abort(self) -> None: ...
        @property
        def is_active(self) -> bool: return True

    h = FakeHandle()
    assert isinstance(h, StreamingMessageHandle)


def test_tool_event_handle_is_runtime_checkable():
    """ToolEventHandle is a runtime_checkable Protocol."""

    class FakeToolHandle:
        def feed_stdout(self, line: str) -> None: ...
        def feed_stderr(self, line: str) -> None: ...
        def feed_diff(self, diff_text: str) -> None: ...
        def commit_success(self, *, summary=None, metadata=None) -> None: ...
        def commit_failure(self, *, error, exit_code=None) -> None: ...
        @property
        def is_active(self) -> bool: return True

    h = FakeToolHandle()
    assert isinstance(h, ToolEventHandle)


def test_non_conforming_object_fails_runtime_check():
    """An object missing required methods fails isinstance()."""

    class NotAHandle:
        pass

    obj = NotAHandle()
    assert not isinstance(obj, StreamingMessageHandle)
    assert not isinstance(obj, ToolEventHandle)


# === Conformance suite for concrete backends ===


class ViewBackendConformanceSuite:
    """Base class for concrete backend test classes.

    Concrete backends (REPLBackendTests, TelegramBackendTests, etc.)
    subclass this and provide a ``backend`` fixture yielding an
    instance. They inherit all tests defined here for free.

    Example:
        class TestREPLBackendConformance(ViewBackendConformanceSuite):
            @pytest.fixture
            async def backend(self, tmp_path):
                b = REPLBackend(config=test_config(tmp_path))
                await b.start()
                yield b
                await b.stop()
    """

    @pytest.fixture
    def backend(self):
        """Subclass must override with a real fixture."""
        pytest.skip(
            "ViewBackendConformanceSuite is abstract; subclass and "
            "override the backend fixture."
        )

    def test_is_view_backend_instance(self, backend):
        """The backend is an instance of ViewBackend."""
        assert isinstance(backend, ViewBackend)

    def test_has_all_abstract_methods_implemented(self, backend):
        """No abstract method leaks through — the backend class must
        have concrete implementations of all abstractmethods."""
        assert not getattr(
            type(backend), "__abstractmethods__", frozenset()
        ), (
            f"{type(backend).__name__} has unimplemented abstract methods: "
            f"{type(backend).__abstractmethods__}"
        )

    def test_set_input_handler_accepts_async_callable(self, backend):
        """set_input_handler stores the handler without raising."""
        async def fake_handler(text: str) -> None:
            pass
        backend.set_input_handler(fake_handler)  # should not raise

    def test_update_status_accepts_partial(self, backend):
        """update_status with a partial StatusUpdate doesn't crash."""
        backend.update_status(StatusUpdate(model="test-model"))
        backend.update_status(StatusUpdate(cost_usd=0.01))
        backend.update_status(StatusUpdate())  # empty partial

    def test_render_message_accepts_all_roles(self, backend):
        """render_message handles every Role without error."""
        for role in Role:
            backend.render_message(MessageEvent(role=role, content=f"test {role.value}"))

    def test_print_methods_accept_string(self, backend):
        """print_info/warning/error/panel accept strings without error."""
        backend.print_info("info line")
        backend.print_warning("warning line")
        backend.print_error("error line")
        backend.print_panel("panel body", title="panel title")
        backend.print_panel("panel without title")  # title is optional

    def test_lifecycle_hooks_are_callable(self, backend):
        """Default no-op lifecycle hooks are safe to call."""
        backend.on_turn_start()
        backend.on_turn_end()
        backend.on_session_compaction(removed_tokens=100)
        backend.on_session_load(session_id="test", message_count=5)
        backend.voice_started()
        backend.voice_progress(seconds=1.0, peak=0.5)
        backend.voice_stopped(reason="manual_stop")
        backend.clear_screen()
        backend.mark_fatal_error(code="TEST", message="test error", retryable=True)
```

- [ ] **Step 3: Run the tests**

Run: `/Users/adamhong/miniconda3/bin/python3 -m pytest tests/test_view/test_protocol_conformance.py -v`

Expected: all Protocol-level tests pass (the `ViewBackendConformanceSuite` tests are skipped because there's no concrete backend yet — that's correct).

- [ ] **Step 4: Verify the expected test count**

Run: `/Users/adamhong/miniconda3/bin/python3 -m pytest tests/test_view/test_protocol_conformance.py --collect-only -q 2>&1 | tail -5`

Expected: ~18 Protocol-level tests collected + some ConformanceSuite tests that skip.

- [ ] **Step 5: Commit**

```bash
git add tests/test_view/__init__.py tests/test_view/test_protocol_conformance.py
git commit -m "test(view): add Protocol conformance test harness"
```

---

### Task 1.6: Write ADDING_A_BACKEND.md contributor doc

**Files:**
- Create: `llm_code/view/ADDING_A_BACKEND.md`

- [ ] **Step 1: Write the contributor doc**

Write `llm_code/view/ADDING_A_BACKEND.md` with the following content:

````markdown
# Adding a new ViewBackend

This doc is for contributors adding a new `ViewBackend` implementation
to llmcode — for example, a Telegram bot frontend, a Discord bot, a
WebSocket-based web UI, or a Slack interactive app.

The existing `REPLBackend` (in `llm_code/view/repl/`) is the reference
implementation. When in doubt, read how REPL does it.

## Prerequisites

- Read `docs/superpowers/specs/2026-04-11-llm-code-repl-mode-design.md`
  sections 4 (Architecture) and 5 (ViewBackend Protocol).
- Understand the `ViewBackend` ABC in `llm_code/view/base.py`.
- Understand the data types in `llm_code/view/types.py` (MessageEvent,
  StatusUpdate, Role, RiskLevel, StreamingMessageHandle, ToolEventHandle).
- Understand the dialog types in `llm_code/view/dialog_types.py`
  (Choice, TextValidator, DialogCancelled).

## Directory layout

Put your backend under `llm_code/view/<platform>/`. For example:

```
llm_code/view/
├── __init__.py
├── base.py
├── types.py
├── dialog_types.py
├── dispatcher.py
├── repl/           # existing reference backend
│   └── ...
└── telegram/       # your new backend
    ├── __init__.py
    ├── backend.py  # class TelegramBackend(ViewBackend)
    ├── renderers.py
    └── ...
```

The `backend.py` module should export a single class `<Platform>Backend`
that inherits from `ViewBackend`.

## Required abstract methods

Every backend must implement these 17 methods (see `view/base.py` for
full signatures):

**Lifecycle** (3): `start`, `stop`, `run`

**Input** (1): `set_input_handler`

**Message output** (2): `render_message`, `start_streaming_message`

**Tool events** (1): `start_tool_event`

**Status** (1): `update_status`

**Dialogs** (4): `show_confirm`, `show_select`, `show_text_input`,
`show_checklist`

**Convenience output** (4): `print_info`, `print_warning`, `print_error`,
`print_panel`

**External editor** (1): `open_external_editor`

## Optional hooks

These have default no-op implementations. Override if your backend
has a sensible reaction:

- `mark_fatal_error(code, message, retryable)`
- `voice_started()` / `voice_progress(seconds, peak)` / `voice_stopped(reason)`
- `clear_screen()`
- `on_turn_start()` / `on_turn_end()`
- `on_session_compaction(removed_tokens)` / `on_session_load(session_id, message_count)`

Bot backends typically don't implement voice UI (the user isn't looking
at a screen) or clear_screen. Web backends implement them all.

## Push-model input handling

Backends are push-model: your `run()` method reads/receives input from
whatever source (PTY for REPL, webhook for Telegram, WebSocket for web)
and calls `await self._input_handler(text)` for each complete submission.
Register the handler at startup via `set_input_handler(callback)` — the
dispatcher does this automatically during llmcode boot.

Don't try to invert this to a pull model; the dispatcher and runtime
assume push semantics universally.

## Streaming messages

`start_streaming_message(role)` returns a `StreamingMessageHandle`
that the dispatcher feeds chunks into:

```python
handle = backend.start_streaming_message(role=Role.ASSISTANT)
for chunk in llm_stream:
    handle.feed(chunk.text)
handle.commit()
```

Your handle implementation decides how to render the in-progress stream.
REPL uses a Rich Live region that refreshes in place, then commits to
scrollback. A Telegram backend might edit a single message over and
over via `editMessageText`, then leave it in final form. A web backend
might push incremental chunks over a WebSocket.

Key invariants:

- `feed()` is callable any number of times before `commit()` or `abort()`.
- `commit()` finalizes and makes the message permanent/visible.
- `abort()` discards the in-progress message (called on Ctrl+C cancel
  or dispatcher error).
- After `commit()`/`abort()`, further `feed()` calls should be no-ops
  (not errors).
- `is_active` is True between start and commit/abort.

## Tool events

`start_tool_event(tool_name, args)` returns a `ToolEventHandle`. The
dispatcher feeds stdout/stderr/diff lines in, then calls `commit_success`
or `commit_failure`.

Style R (REPL's default): inline summary line on start and commit;
automatic expansion for diff-producing tools (edit_file/write_file/
apply_patch) and failures. Bot backends typically render a compact
summary only and link to full output.

## Dialogs

The four `show_*` methods are the user-interaction primitives. REPL
implements them as `prompt_toolkit` Float overlays. Bot backends
typically use inline keyboard components (Telegram, Slack). Web
backends use modal overlays.

Must raise `DialogCancelled` when the user cancels (Esc, back button,
timeout, etc.). Callers catch this and abort the higher-level operation.

## Testing

Your backend must pass the `ViewBackendConformanceSuite` from
`tests/test_view/test_protocol_conformance.py`:

```python
# tests/test_view/test_telegram_backend.py
import pytest
from tests.test_view.test_protocol_conformance import ViewBackendConformanceSuite
from llm_code.view.telegram.backend import TelegramBackend

class TestTelegramBackendConformance(ViewBackendConformanceSuite):
    @pytest.fixture
    async def backend(self, mock_telegram_api):
        b = TelegramBackend(api=mock_telegram_api)
        await b.start()
        yield b
        await b.stop()
```

All the inherited tests should pass without additional work if your
backend respects the Protocol.

Beyond conformance, write backend-specific tests for your
platform-specific quirks (Telegram rate limits, Slack thread handling,
WebSocket reconnect, etc.).

## Registration

Once your backend is done and tests pass, register it in
`llm_code/cli/main.py`:

```python
# v2.0.0: only REPL
backend = REPLBackend(config=config, runtime=runtime)

# v2.1.0+: registry lookup by config
backend_name = config.view_backend  # "repl", "telegram", ...
backend_cls = VIEW_BACKEND_REGISTRY[backend_name]
backend = backend_cls(config=config, runtime=runtime)
```

(The registry itself lands in v2.1.0 along with the first non-REPL
backend; v2.0.0 hardcodes REPL.)

## What NOT to do

- Don't import `prompt_toolkit` or `rich` outside your backend's own
  package. The Protocol is deliberately library-agnostic.
- Don't inspect or mutate `runtime.conversation`, `runtime.cost_tracker`,
  or other runtime state directly. The dispatcher is the only consumer
  of runtime; your backend talks to the dispatcher via the Protocol.
- Don't assume the user has a screen. Telegram users don't see live
  status updates; your `update_status` may be a no-op. That's fine.
- Don't block the asyncio event loop. All I/O must be async-friendly.
  If you need blocking work (e.g., calling a sync SDK), use
  `asyncio.to_thread` or a dedicated executor.
- Don't bypass the dispatcher to call LLM APIs directly. The dispatcher
  owns turn lifecycle, cost tracking, and permission checks. Your
  backend's job is I/O + presentation only.
````

- [ ] **Step 2: Commit**

```bash
git add llm_code/view/ADDING_A_BACKEND.md
git commit -m "docs(view): contributor guide for adding new ViewBackend implementations"
```

---

### Task 1.7: Run all view tests and verify green

**Files:** none (verification)

- [ ] **Step 1: Run the view test suite**

Run: `/Users/adamhong/miniconda3/bin/python3 -m pytest tests/test_view/ -v`

Expected: ~18 tests pass, ~7 ConformanceSuite tests skipped (pytest emits `s` for each).

- [ ] **Step 2: Verify no Python import errors anywhere in the package**

Run:
```bash
/Users/adamhong/miniconda3/bin/python3 -c "
from llm_code.view import base, types, dialog_types
from llm_code.view.base import ViewBackend
from llm_code.view.types import (
    MessageEvent, StatusUpdate, Role, RiskLevel,
    StreamingMessageHandle, ToolEventHandle,
)
from llm_code.view.dialog_types import (
    Choice, TextValidator, DialogCancelled, DialogValidationError,
)
print('all view symbols import cleanly')
"
```

Expected: `all view symbols import cleanly`.

- [ ] **Step 3: Push the branch**

Run: `git push origin feat/repl-mode`

Expected: branch updates on origin.

---

## Milestone completion criteria

M1 is considered complete when:

- ✅ `feat/repl-mode` branch exists, tracking `origin/feat/repl-mode`
- ✅ `llm_code/view/__init__.py` exists (empty)
- ✅ `llm_code/view/dialog_types.py` exists and imports cleanly
- ✅ `llm_code/view/types.py` exists and imports cleanly
- ✅ `llm_code/view/base.py` exists; `ViewBackend` has all 17 abstractmethods; cannot be instantiated directly
- ✅ `llm_code/view/ADDING_A_BACKEND.md` exists
- ✅ `tests/test_view/test_protocol_conformance.py` exists with ~18 passing Protocol-level tests
- ✅ `ViewBackendConformanceSuite` base class defined and importable (used in M3+)
- ✅ All commits pushed to `origin/feat/repl-mode`
- ✅ No changes to `main` branch
- ✅ No changes to existing `llm_code/` production code (only new files under `llm_code/view/`)

## Estimated effort

- Task 1.0 (branch): 2 minutes
- Task 1.1 (view scaffold): 3 minutes
- Task 1.2 (dialog_types): 15 minutes
- Task 1.3 (types): 25 minutes
- Task 1.4 (base ABC): 40 minutes (longest task — the ABC itself)
- Task 1.5 (conformance tests): 45 minutes
- Task 1.6 (contributor doc): 30 minutes (mostly prose)
- Task 1.7 (verification): 5 minutes

**Total: ~2.5 hours** for a single focused session.

## Why this milestone exists

M1 establishes the contract that every subsequent milestone depends on:

- M2 (REPLPilot fixture) tests against the Protocol
- M3 (ScreenCoordinator skeleton) delegates to Protocol methods
- M4–M9 (components) implement Protocol methods
- M10 (dispatcher relocation) rewrites the dispatcher against the Protocol
- M11 (cutover) deletes tui/ with confidence because the replacement surface is already proven

Getting the Protocol right up front costs 2.5 hours; getting it wrong and having to refactor mid-project costs days. The conformance test harness (Task 1.5) is the safety net that catches drift early — any M3+ backend implementation that forgets a method or changes a signature fails the harness immediately.

## Next milestone

After M1 is complete and pushed, proceed to **M2 — REPLPilot test abstraction**
(plan file: `2026-04-11-llm-code-repl-m2-pilot.md`).
