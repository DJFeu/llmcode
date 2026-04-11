# LLM-Code REPL Mode: Design Spec (v2.0.0)

**Date:** 2026-04-11
**Author:** Adam
**Status:** Approved (pending implementation plan)
**Scope:** Complete replacement of Textual fullscreen TUI with a line-streaming REPL mode built on `prompt_toolkit` + `Rich`, plus introduction of a `ViewBackend` Protocol that positions llmcode to grow future Telegram/Discord/Slack/Web backends.
**Target release:** v2.0.0 (preceded by v1.23.1 stopgap patch already shipped)

---

## 1. Motivation

### 1.1 The pain that drove this spec

Over v1.16 through v1.23.0, the Textual fullscreen TUI accumulated a class of bugs that are *structural*, not accidental:

- **Mouse capture setting flipped four times** across the project's history (v1.16 → v1.17 → intermediate revert → intermediate revert → v1.17.0 "TUI scroll fix"). Each flip traded one bug for another. The root cause is that Textual's `app.run(mouse=True/False)` is a single knob controlling three conflated things: in-app wheel scrolling, native click-drag text selection, and terminal byte-stream routing. No setting of this knob satisfies all three.
- **Scroll-wheel + alternate scroll (DECSET ?1007)** — In Warp (and other modern terminals), wheel events in alt-screen mode without mouse tracking get translated to bare Up/Down arrow keystrokes. These keystrokes landed on `InputBar`, which had history recall hardcoded to bare Up/Down, so every wheel-scroll rewound the input buffer to the previous command (typically `/voice`, making it look like voice was self-triggering). Fixed in v1.23.1 by moving history recall to Ctrl+↑/↓ and emitting `?1007l` on mount — but the underlying issue is that *any* fullscreen TUI that lives in alt-screen mode will encounter the next variant of this class of bug.
- **Selection + copy fails for many users.** Textual's mouse capture blocks native click-drag text selection unless the user holds Option (macOS) — a workaround that must be taught to every new user.
- **`/scroll` / Shift+↑↓ / PageUp/Down** are all workarounds for "the mouse wheel should just scroll the terminal."

After the fourth flip, it became clear: **the fullscreen TUI itself is the source of the pain, not any particular setting of it.** The durable fix is to stop running inside an alt-screen buffer at all — to let the terminal handle scrollback, text selection, Find, and AI block recognition natively, and reserve only the bottom few lines for the input prompt and status line.

### 1.2 The strategic opportunity

Nous Research's **hermes-agent** (MIT) ships a `gateway/platforms/` package with 18 platform adapters (Telegram, Discord, Slack, WhatsApp, Signal, Matrix, Mattermost, Email, Webhook, SMS, Bluebubbles, DingTalk, Feishu, WeChat, WeCom, HomeAssistant, API server, Telegram Network) all inheriting from a shared `BasePlatformAdapter` ABC in `gateway/platforms/base.py`. The adapter surface (`connect`, `disconnect`, `send`, `edit_message`, `send_typing`, plus `MessageEvent` / `SendResult` data types) is production-validated by 18 real consumers.

This makes it cheap for llmcode to adopt the same philosophy — not by forking hermes-agent, but by mirroring its architectural spine. If llmcode replaces its TUI with a `ViewBackend` Protocol whose first implementation is the new REPL, the second and third implementations (TelegramBackend, DiscordBackend, …) become straightforward additions later, borrowing hermes-agent's adapter signatures as the design reference. **Protocol-first is not premature abstraction here because the Protocol shape is already validated.**

### 1.3 Decision: Replace the TUI, don't coexist

After brainstorming, the user committed to **Path A (complete replacement)** with **Strategy b (llmcode evolves into a llmcode-flavoured hermes-agent)**. The Textual fullscreen TUI is deleted in v2.0.0; the new REPL is the only frontend at v2.0.0 release, with the Protocol ready for v2.1+ platform additions.

---

## 2. Goals and Non-Goals

### 2.1 Goals

- **G1.** Eliminate the `Textual fullscreen + alt-screen + mouse capture` class of bugs permanently.
- **G2.** Let the terminal handle scrollback, text selection, Find, copy, Warp AI blocks, OSC8 hyperlinks, and terminal multiplexer (tmux/screen) compatibility natively.
- **G3.** Deliver a Claude Code-style streaming REPL experience: Live status line, in-place streaming Markdown, slash command popover, inline dialog overlays.
- **G4.** Introduce `ViewBackend` Protocol as the extension point for future non-CLI backends (Telegram, Discord, Slack, Web, API server).
- **G5.** Preserve the slash command surface (minus 4 legacy-only commands documented in §7.2: `/scroll`, `/marketplace browse`, `/plugin browse`, `/settings` modal — 62 → 58 total), prompt history, voice input, streaming tool calls, multi-line input, vim mode, external editor integration.
- **G6.** Ship a v1.23.1 patch that restores native text selection and fixes the scroll-wheel-history collision on the existing Textual TUI, so users have a stable baseline during v2.0.0 development. ✅ **Already done** at commit `bf72f970`.
- **G7.** Replace the 657 Textual TUI tests with an equivalent or stronger REPL test suite, using progressive transliteration (milestone-by-milestone) rather than wholesale deletion.

### 2.2 Non-goals (for v2.0.0)

- **NG1.** Ship more than one `ViewBackend` in v2.0.0. Only REPL. Platform backends land in v2.1+.
- **NG2.** Preserve the Quick Open "preview pane" (two-column fuzzy + content preview). Replaced by `radiolist_dialog` + automatic first-20-lines preview into scrollback.
- **NG3.** Preserve the Marketplace "card grid" visual. Replaced by `radiolist_dialog` list + metadata panel on selection.
- **NG4.** Preserve `/scroll` slash command — terminal-native scrollback supersedes it.
- **NG5.** Ship fork of hermes-agent or direct port of any of their code. Borrow design signatures only.
- **NG6.** Break existing sessions, prompt history, or config files. v1.x session checkpoints must load in v2.0.0.
- **NG7.** Image paste preview as ASCII art. Replaced by metadata-only display (optionally OSC 1337 inline image in Warp/iTerm2 as future enhancement; **not** v2.0.0 scope).

---

## 3. Strategic Context: Borrowing from hermes-agent

### 3.1 What we borrow

From `gateway/platforms/base.py:726` (`class BasePlatformAdapter(ABC)`) and the surrounding types:

- **The Protocol-as-extension-point philosophy.** A single abstract base with a clean surface, many concrete backends.
- **Lifecycle methods:** `connect()` / `disconnect()` / fatal-error propagation.
- **Data types:** `MessageEvent` (tagged input event), `SendResult` (tagged output result), `Platform` / `MessageType` / `ProcessingOutcome` enums.
- **Session interrupt pattern:** active-session tracking + asyncio.Event for interrupt signals, so Ctrl+C-style cancellation works across backends.
- **Background task tracking:** each adapter holds `_background_tasks: set[asyncio.Task]` so a backend swap cancels in-flight work cleanly.
- **Push model for input:** backends own the input source (webhook, PTY, etc.) and notify the dispatcher via a callback. Dispatcher never pulls synchronously. (This fixes the fundamental REPL-vs-bot asymmetry by standardizing on push.)
- **"Adding a platform" developer doc:** hermes-agent ships `gateway/platforms/ADDING_A_PLATFORM.md` as a checklist for contributors. llmcode will mirror this at `llm_code/view/ADDING_A_BACKEND.md`.

### 3.2 What we do NOT borrow

- Not porting the 1100-line `BasePlatformAdapter` wholesale. llmcode's Protocol is view-specific (~200 lines) — platform concerns like `send_typing`, `chat_id`, `metadata.thread_id` either get simplified or pushed to platform-specific subclasses later.
- Not adopting hermes-agent's gateway process model (multi-platform concurrent running, mirror/forwarding between platforms). llmcode runs **one backend per process** at v2.0.0.
- Not adopting hermes-agent's platform-specific features that don't map to CLI (typing indicators, voice TTS on Telegram, emoji reactions).

### 3.3 Naming

- Their concept: **Platform Adapter** (Telegram, Discord, etc. — messenger platforms).
- Our concept: **View Backend** (REPL, future Telegram, future Web — how the user interacts with llmcode).
- Rationale for not reusing their name: a REPL backend isn't a "platform" in the messenger sense. `ViewBackend` is broader — it covers "how does this agent present itself and receive input," which includes both CLI-style direct views and platform-messenger-style relay views.

---

## 4. Architecture Overview

### 4.1 Package layout

```
llm_code/
├── runtime/             # unchanged — config, conversation, hooks, fallback, recovery
├── api/                 # unchanged — provider client, streaming, types
├── tools/               # unchanged — registry, voice, bash, edit, etc.
├── memory/              # unchanged
├── recovery/            # unchanged
│
├── view/                # NEW — all view-layer code
│   ├── __init__.py
│   ├── base.py          # ViewBackend ABC + abstract methods
│   ├── types.py         # MessageEvent, StatusUpdate, RiskLevel,
│   │                    # StreamingMessageHandle, ToolEventHandle
│   ├── dispatcher.py    # relocated from tui/command_dispatcher.py,
│   │                    # widget references replaced with Protocol calls
│   ├── headless.py      # relocated from tui/dialogs/headless.py
│   ├── ADDING_A_BACKEND.md  # contributor doc for new backends
│   └── repl/            # REPL is the first (and v2.0.0 only) ViewBackend
│       ├── __init__.py
│       ├── backend.py   # class REPLBackend(ViewBackend)
│       ├── coordinator.py  # ScreenCoordinator — sole owner of
│       │                   # Rich Live + prompt_toolkit Application
│       ├── keybindings.py  # prompt_toolkit KeyBindings definitions
│       ├── history.py      # relocated from tui/prompt_history.py
│       ├── theme.py        # Rich theme configuration
│       ├── snapshots.py    # snapshot test helpers
│       └── components/
│           ├── status_line.py
│           ├── input_area.py
│           ├── slash_popover.py
│           ├── live_response_region.py  # Strategy Z
│           ├── tool_event_renderer.py   # Style R
│           ├── dialog_popover.py
│           └── voice_overlay.py
│
├── cli/
│   ├── main.py          # renamed from tui_main.py; wires backend + dispatcher
│   ├── oneshot.py       # unchanged — -q / -x modes
│   ├── streaming.py     # unchanged — IncrementalMarkdownRenderer still used
│   ├── render.py        # unchanged
│   ├── commands.py      # unchanged — COMMAND_REGISTRY source
│   └── status_line.py   # absorbed into view/repl/components/status_line.py
│
└── tui/                 # DELETED in v2.0.0
    # All 28 files removed; utility-only modules (prompt_history, keybindings,
    # ansi_strip, double_press) relocated into view/repl/ or view/.
```

### 4.2 Layer diagram

```
┌──────────────────────────────────────────────────────────────┐
│  Entry Point                                                 │
│  cli/main.py  →  instantiates backend, starts event loop     │
└──────────────────────┬───────────────────────────────────────┘
                       │ knows concrete backend type
                       ▼
┌──────────────────────────────────────────────────────────────┐
│  View Backend Implementations                                │
│    view/repl/backend.py   ← REPLBackend (v2.0.0 only)        │
│    (future) view/telegram/backend.py                         │
│    (future) view/discord/backend.py                          │
│    (future) view/slack/backend.py                            │
│    (future) view/web/backend.py (WebSocket JSON events)      │
└──────────────────────┬───────────────────────────────────────┘
                       │ implements
                       ▼
┌──────────────────────────────────────────────────────────────┐
│  ViewBackend Protocol  (view/base.py + view/types.py)        │
│    ~20 abstract methods + ~10 default-noop lifecycle hooks   │
└──────────────────────┬───────────────────────────────────────┘
                       │ view-agnostic; depends on the Protocol
                       ▼
┌──────────────────────────────────────────────────────────────┐
│  View Dispatcher  (view/dispatcher.py)                       │
│    58 slash commands (62 − 4 legacy cut in M10),             │
│    0 widget references; interacts with                       │
│    `self._view: ViewBackend` only                            │
└──────────────────────┬───────────────────────────────────────┘
                       │ depends on (existing, untouched)
                       ▼
┌──────────────────────────────────────────────────────────────┐
│  Runtime Core  (runtime/, api/, tools/, memory/, recovery/)  │
└──────────────────────────────────────────────────────────────┘
```

**Invariant:** dispatcher and runtime code contains zero references to `prompt_toolkit`, `Rich`, `REPLBackend`, or any view implementation. The only thing they see is the `ViewBackend` Protocol.

### 4.3 Dependencies

New runtime dependencies:

- `prompt_toolkit>=3.0.47` — input handling, KeyBindings, full_screen=False Application mode, slash completer, vim mode, test session API
- `rich>=13.7.0` — already a dependency; used for `Live`, `Markdown`, `Panel`, `Syntax`, `Console`

New dev/test dependencies:

- `pexpect>=4.9.0` — E2E smoke tests
- `syrupy>=4.6.0` — snapshot testing (optional; may use pytest's built-in mechanism instead)

Dependencies being removed:

- `textual>=0.x` — no longer needed after v2.0.0
- No other Textual-ecosystem packages currently in use

---

## 5. ViewBackend Protocol

### 5.1 Abstract base

```python
# llm_code/view/base.py
from abc import ABC, abstractmethod
from typing import Optional, Sequence, Dict, Any, TypeVar, Callable, Awaitable

from llm_code.view.types import (
    MessageEvent,
    StatusUpdate,
    Role,
    RiskLevel,
    StreamingMessageHandle,
    ToolEventHandle,
)
from llm_code.view.dialog_types import Choice, TextValidator

T = TypeVar("T")


class ViewBackend(ABC):
    """Protocol for all user-facing backends.

    First implementation: REPLBackend (v2.0.0).
    Future: TelegramBackend, DiscordBackend, SlackBackend, WebBackend.

    Design derived from Nous Research's hermes-agent BasePlatformAdapter
    (gateway/platforms/base.py), simplified to view-only concerns.
    """

    # === Lifecycle ===
    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...

    @abstractmethod
    async def run(self) -> None:
        """Main event loop. Returns when user requests exit (Ctrl+D, /quit)."""

    def mark_fatal_error(
        self,
        code: str,
        message: str,
        retryable: bool = True,
    ) -> None:
        """Notify backend of an unrecoverable runtime error."""

    # === Input (push model, mirroring hermes-agent) ===
    @abstractmethod
    def set_input_handler(
        self,
        handler: Callable[[str], Awaitable[None]],
    ) -> None:
        """Install the async callback invoked on each user-submitted input.

        Backend's `run()` internally reads/receives input and calls
        `await handler(text)`. REPL reads via prompt_toolkit; Telegram
        receives via webhook; Web receives via WebSocket — all converge
        on this push-model callback.
        """

    # === Output: messages ===
    @abstractmethod
    def render_message(self, event: MessageEvent) -> None:
        """Render a complete (non-streaming) message: user echo, system
        note, compaction marker, etc."""

    @abstractmethod
    def start_streaming_message(
        self,
        role: Role,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> StreamingMessageHandle:
        """Begin a streaming assistant response. Returns a handle to feed
        chunks into and commit when done. REPL backend implements via
        Strategy Z (Rich Live region + commit to scrollback)."""

    # === Output: tool events ===
    @abstractmethod
    def start_tool_event(
        self,
        tool_name: str,
        args: Dict[str, Any],
    ) -> ToolEventHandle:
        """Begin a tool call display. REPL backend implements Style R:
        inline summary by default, diff tools (edit_file/write_file/
        apply_patch) and failures auto-expand."""

    # === Output: status ===
    @abstractmethod
    def update_status(self, status: StatusUpdate) -> None:
        """Update the persistent status display (model, cost, tokens,
        voice state, etc.). Partial updates — only non-None fields apply."""

    # === Dialogs ===
    @abstractmethod
    async def show_confirm(
        self,
        prompt: str,
        default: bool = False,
        risk: RiskLevel = RiskLevel.NORMAL,
    ) -> bool: ...

    @abstractmethod
    async def show_select(
        self,
        prompt: str,
        choices: Sequence[Choice[T]],
        default: Optional[T] = None,
    ) -> T: ...

    @abstractmethod
    async def show_text_input(
        self,
        prompt: str,
        default: Optional[str] = None,
        validator: Optional[TextValidator] = None,
        secret: bool = False,
    ) -> str: ...

    @abstractmethod
    async def show_checklist(
        self,
        prompt: str,
        choices: Sequence[Choice[T]],
        defaults: Optional[Sequence[T]] = None,
    ) -> Sequence[T]: ...

    # === Voice notifications (called BY recorder, not dispatcher) ===
    def voice_started(self) -> None:
        """Default no-op; REPL overrides to show recording overlay."""
        pass

    def voice_progress(self, seconds: float, peak: float) -> None:
        """Periodic update during active recording."""
        pass

    def voice_stopped(self, reason: str) -> None:
        """Called when recording ends (VAD auto-stop, manual stop, error)."""
        pass

    # === Convenience output ===
    @abstractmethod
    def print_info(self, text: str) -> None: ...

    @abstractmethod
    def print_warning(self, text: str) -> None: ...

    @abstractmethod
    def print_error(self, text: str) -> None: ...

    @abstractmethod
    def print_panel(
        self,
        content: str,
        title: Optional[str] = None,
    ) -> None: ...

    def clear_screen(self) -> None:
        """Default no-op; backends that support it override."""
        pass

    # === Session events (default no-ops; hooks for backends to react) ===
    def on_turn_start(self) -> None: pass
    def on_turn_end(self) -> None: pass
    def on_session_compaction(self, removed_tokens: int) -> None: pass
    def on_session_load(self, session_id: str, message_count: int) -> None: pass

    # === External editor ===
    @abstractmethod
    async def open_external_editor(
        self,
        initial_text: str = "",
        filename_hint: str = ".md",
    ) -> str:
        """Open $EDITOR (REPL) or equivalent upload interface (Telegram
        long-message compose, Web modal). Returns edited content."""
```

### 5.2 Data types

```python
# llm_code/view/types.py
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, Dict, Any, Protocol


class Role(Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


@dataclass(frozen=True)
class MessageEvent:
    role: Role
    content: str
    timestamp: datetime = field(default_factory=datetime.now)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class StatusUpdate:
    """Partial update — only non-None fields are applied."""
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


class StreamingMessageHandle(Protocol):
    """Handle to an active streaming message region."""
    def feed(self, chunk: str) -> None: ...
    def commit(self) -> None: ...
    def abort(self) -> None: ...
    @property
    def is_active(self) -> bool: ...


class ToolEventHandle(Protocol):
    """Handle to an active tool call display."""
    def feed_stdout(self, line: str) -> None: ...
    def feed_stderr(self, line: str) -> None: ...
    def feed_diff(self, diff_text: str) -> None: ...

    def commit_success(
        self,
        *,
        summary: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None: ...

    def commit_failure(
        self,
        *,
        error: str,
        exit_code: Optional[int] = None,
    ) -> None: ...

    @property
    def is_active(self) -> bool: ...


class RiskLevel(Enum):
    """Classification used by show_confirm() to color dialogs by risk."""
    NORMAL = "normal"       # read_file, ls, git status
    ELEVATED = "elevated"   # edit_file, bash (read-only), write_file (in cwd)
    HIGH = "high"           # bash (mutating), write_file outside cwd, net ops
    CRITICAL = "critical"   # delete files, git push --force, rm -rf
```

### 5.3 Dispatcher usage pattern

The dispatcher owns only a reference to `ViewBackend` (the Protocol). It never imports any concrete backend.

```python
# llm_code/view/dispatcher.py (example — full file generated in M10)
class CommandDispatcher:
    def __init__(self, view: ViewBackend, runtime: Runtime):
        self._view = view
        self._runtime = runtime

    async def run_turn(self, user_input: str) -> None:
        # Echo user message to history
        self._view.render_message(MessageEvent(
            role=Role.USER, content=user_input,
        ))

        self._view.on_turn_start()
        handle = self._view.start_streaming_message(role=Role.ASSISTANT)

        try:
            async for chunk in self._runtime.client.stream(user_input):
                if chunk.text:
                    handle.feed(chunk.text)

                if chunk.tool_call:
                    if chunk.tool_call.requires_approval:
                        approved = await self._view.show_confirm(
                            prompt=f"Allow {chunk.tool_call.name}?",
                            default=False,
                            risk=chunk.tool_call.risk,
                        )
                        if not approved:
                            te = self._view.start_tool_event(
                                chunk.tool_call.name, chunk.tool_call.args,
                            )
                            te.commit_failure(error="user denied")
                            continue

                    te = self._view.start_tool_event(
                        chunk.tool_call.name, chunk.tool_call.args,
                    )
                    try:
                        result = await self._runtime.tools.run(chunk.tool_call)
                        if hasattr(result, "diff"):
                            te.feed_diff(result.diff)
                        te.commit_success(summary=result.summary)
                    except Exception as e:
                        te.commit_failure(error=str(e))

            handle.commit()

        except asyncio.CancelledError:
            handle.abort()
            raise

        finally:
            self._view.on_turn_end()
            self._view.update_status(StatusUpdate(
                cost_usd=self._runtime.cost_tracker.total_cost,
                context_used_tokens=self._runtime.conversation.token_count,
                is_streaming=False,
            ))
```

---

## 6. REPL Backend Internals

### 6.1 ScreenCoordinator — the central brain

`view/repl/coordinator.py` owns both `prompt_toolkit.Application` and `rich.Live`. Every "the screen went weird" bug has a single place to land.

**Invariant:** only one of (PT redraw, Rich Live refresh) writes to stdout at a time. Protected by `asyncio.Lock`.

**Responsibilities:**
- Build the `Layout` (see 6.2).
- Create and drive `Rich.Live` for streaming response + tool event regions.
- Broker input → dispatcher handler calls.
- Handle terminal resize events.
- Coordinate voice UI updates from background-thread callbacks via `loop.call_soon_threadsafe`.

### 6.2 Bottom layout (Layout 1 from brainstorm)

```
╭─ scrollback (native terminal) ────────────────────╮
│                                                   │
│  (all chat history, streaming commits,            │
│   tool events, errors — flows naturally into       │
│   terminal scrollback, copy/find/scroll native)   │
│                                                   │
├───────────────────────────────────────────────────┤
│ ⚠ rate limited · retry 14:23 · 5 reqs left        │  ← ConditionalContainer
├───────────────────────────────────────────────────┤  ← (shown only when
│ Q3.5-122B · llm-code(main) · 16k tok · $0.00      │     rate-limited)
├───────────────────────────────────────────────────┤
│ > /voice _                                        │
│   ▼ /voice    Toggle voice input                  │  ← SlashPopover Float
│     /version  Show llmcode version                │     (shown on / prefix)
│     /vim      Toggle vim mode                     │
╰───────────────────────────────────────────────────╯
                                                       ← terminal bottom
```

**Components:**

- **Rate limit warning** — `ConditionalContainer(Window(rate_limit_line), filter=HasRateLimitFilter)` — hidden unless runtime is in rate-limited state. Red text.
- **Status line** — 1 line. Format: `{model} · {cwd}({branch}) · {ctx_used}/{ctx_limit} tok · ${cost}` with optional spinner `⠋ 1.2k tok` on the right during streaming. During voice recording, the *entire line* is replaced with `🎙 0:02.3 · peak 0.42 · Ctrl+G stop` in dim red.
- **Input area** — `Window(BufferControl(...))` with `multiline=True`. Grows from 1 line to max 12 lines as the user types.
- **Slash popover** — `Float(ConditionalContainer(CompletionsMenu(...)))` above the input when `value.startswith("/")`.
- **Dialog popover** — another `Float` for `show_confirm/select/text/checklist`, takes focus until answered.

### 6.3 Streaming rendering (Strategy Z)

```python
# llm_code/view/repl/components/live_response_region.py
class LiveResponseRegion:
    """Rich Live region that renders streaming Markdown above the
    PT-reserved area. On commit(), the rendered Markdown is printed to
    scrollback as permanent output and the Live region disappears."""

    REFRESH_HZ = 10  # 100ms per frame

    def __init__(self, console, coordinator, role):
        self._console = console
        self._coordinator = coordinator
        self._role = role
        self._buffer = ""
        self._live: Optional[Live] = None
        self._committed = False

    def start(self) -> None:
        self._live = Live(
            self._render(),
            console=self._console,
            refresh_per_second=self.REFRESH_HZ,
            transient=True,   # region clears itself on stop
            auto_refresh=True,
        )
        self._live.start()

    def feed(self, chunk: str) -> None:
        if self._committed:
            return
        self._buffer += chunk
        self._live.update(self._render())

    def commit(self) -> None:
        if self._committed:
            return
        self._committed = True
        self._live.stop()  # transient=True clears the live region
        # Print the FINAL rendered Markdown to scrollback (permanent).
        from rich.markdown import Markdown
        self._console.print(Markdown(self._buffer))

    def abort(self) -> None:
        if self._committed:
            return
        self._committed = True
        self._live.stop()
        # Don't print to scrollback — the response was cancelled.

    def _render(self):
        from rich.markdown import Markdown
        from rich.panel import Panel
        return Panel(
            Markdown(self._buffer + "▋"),   # cursor indicator
            border_style="cyan",
            title=f"[dim]{self._role.value}[/dim]",
            title_align="left",
        )
```

**In-progress vs final render differ deliberately:**
- In-progress: wrapped in a `Panel` with a border and cursor glyph — visually distinct as "being written now."
- Final: plain `Markdown`, no panel, goes to scrollback as clean permanent content.

The user scrolling up during streaming **does not affect** the Live region (it refreshes in place above the PT layout). The final commit lands as normal terminal output in scrollback, copyable and searchable like any other text.

### 6.4 Tool event rendering (Style R)

```python
# llm_code/view/repl/components/tool_event_renderer.py
class ToolEventRegion:
    """Tool call display with inline summary + auto-expand for diffs/errors."""

    AUTO_EXPAND_TOOLS = frozenset({"edit_file", "write_file", "apply_patch"})

    def __init__(self, console, coordinator, tool_name, args):
        self._console = console
        self._coordinator = coordinator
        self._tool_name = tool_name
        self._args = args
        self._stdout = []
        self._stderr = []
        self._diff_text = ""
        self._committed = False
        self._start_time = time.monotonic()
        self._print_start_line()

    def _print_start_line(self):
        summary = self._format_args_summary(self._args)
        self._console.print(
            f"[dim]▶[/dim] {self._tool_name} {summary}"
        )

    def feed_stdout(self, line): self._stdout.append(line)
    def feed_stderr(self, line): self._stderr.append(line)
    def feed_diff(self, diff_text): self._diff_text = diff_text

    def commit_success(self, *, summary=None, metadata=None):
        if self._committed: return
        self._committed = True
        elapsed = time.monotonic() - self._start_time

        # Diff tools always auto-expand the diff, regardless of verbosity.
        if self._tool_name in self.AUTO_EXPAND_TOOLS and self._diff_text:
            self._render_diff_panel()

        summary_text = summary or self._default_summary()
        self._console.print(
            f"[green]✓[/green] {self._tool_name} · "
            f"{summary_text} · {elapsed:.1f}s"
        )

    def commit_failure(self, *, error, exit_code=None):
        if self._committed: return
        self._committed = True
        elapsed = time.monotonic() - self._start_time

        # Failures always auto-expand stderr (last 12 lines).
        stderr_tail = self._stderr[-12:]
        if stderr_tail:
            self._render_failure_panel(stderr_tail, error)

        exit_str = f" · exit {exit_code}" if exit_code is not None else ""
        self._console.print(
            f"[red]✗[/red] {self._tool_name} · {error} · {elapsed:.1f}s{exit_str}"
        )

    def _render_diff_panel(self):
        from rich.panel import Panel
        from rich.syntax import Syntax
        self._console.print(Panel(
            Syntax(self._diff_text, "diff", theme="ansi_dark"),
            title=f"[bold]{self._tool_name}[/bold] · {self._args.get('path', '')}",
            border_style="cyan",
        ))

    def _render_failure_panel(self, stderr_tail, error):
        from rich.panel import Panel
        self._console.print(Panel(
            "\n".join(stderr_tail),
            title=f"[bold red]✗ {self._tool_name}[/bold red] · {error}",
            border_style="red",
        ))
```

### 6.5 Input handling

Submit / newline / edit:

| Key | Action |
|---|---|
| `Enter` | Submit current input (if non-empty) |
| `Shift+Enter` | Insert newline |
| `Alt+Enter` / `Ctrl+J` / `Ctrl+Enter` | Newline aliases for different terminals |
| `Ctrl+U` | Clear current line |
| `Esc` | Cancel current input |
| `Ctrl+D` (empty input) | Exit llmcode |
| `Ctrl+C` | Cancel current turn (sends asyncio cancel to dispatcher) |
| `Tab` (slash popover open) | Accept selected completion |
| `Tab` (no popover) | Tab-autocomplete slash command |
| `Ctrl+↑` / `Ctrl+↓` | Previous / next history |
| `Ctrl+G` or `Ctrl+Space` | Toggle voice input (lock input, start recording) |
| `Ctrl+X Ctrl+E` | Open current input in `$EDITOR` |

Slash popover behavior:

- **Trigger:** `value.startswith("/")`
- **Navigation:** bare `↑/↓` within popover
- **Accept:** `Tab` (fills input, doesn't submit)
- **Cancel:** `Esc`
- **Max rows:** 8 (4 in terminals shorter than 16 lines); overflow shows `↓ N more`
- **Ctrl+↑/↓ during popover open:** closes popover and runs history recall
- Slash list source: `llm_code/cli/commands.py:COMMAND_REGISTRY` + description strings

### 6.6 Voice integration

Following brainstorm decision **A1 + B1 + C3**:

- **A1 — Global hotkey:** `Ctrl+G` (default) or `Ctrl+Space` (alt). Registered as a prompt_toolkit `KeyBindings` handler at App level. Fires regardless of input state.
- **B1 — Input lock during recording:** When voice starts, input buffer becomes read-only; status line is replaced with the recording indicator `🎙 0:02.3 · peak 0.42 · Ctrl+G stop` in dim red; all keystrokes except Ctrl+G (stop) and Esc (cancel) are ignored.
- **C3 — VAD auto-stop + manual override:** VAD runs the existing `AudioRecorder._has_heard_speech` speech gate + 2-second silence window. Manual stop via Ctrl+G works at any point.

Threading model:

```
main asyncio loop
│
├─ prompt_toolkit.Application event loop
│   └─ KeyBindings dispatcher (Enter, Ctrl+G, etc.)
│
├─ Rich.Live refresh task (10Hz) — coordinator-owned
│
├─ Dispatcher.run_turn() coroutine
│   (feeds StreamingMessageHandle, runs tool calls)
│
└─ AudioRecorder (background thread, sounddevice callback)
    │
    └─ On each chunk: detect peak, run VAD
         │
         └─ Fire loop.call_soon_threadsafe(
              coordinator.voice_progress, seconds, peak
            )
```

**Invariant:** every write to the `prompt_toolkit.Application` state goes through the main asyncio loop. Background thread callbacks use `call_soon_threadsafe` exclusively. This is the one cross-thread boundary in the REPL backend; locking it down prevents the "voice recording + streaming response deadlock" failure mode.

### 6.7 Dialog popovers

`show_confirm`, `show_select`, `show_text_input`, `show_checklist` are implemented as `prompt_toolkit` `Float` overlays above the PT layout. They take focus, block user input until answered, and return via an `asyncio.Future`.

They share signatures with `llm_code/view/headless.py` (the non-Textual dialog backend, relocated from `tui/dialogs/headless.py`), so tests that use the headless backend run identically against REPL dialogs.

---

## 7. User-Facing Behavior (v2.0.0 UX Spec)

### 7.1 What changes from v1.23.x

| Area | v1.23.x | v2.0.0 |
|---|---|---|
| **Overall UI** | Textual fullscreen alt-screen TUI | REPL line-streaming mode, native terminal scrollback |
| **Mouse drag-select copy** | Blocked (need Option+drag on macOS) | Native, no modifier |
| **Scroll-wheel** | In-app capture (broken in Warp) | Native terminal scrollback (native wheel, Cmd+↑↓, PageUp/PageDown) |
| **Terminal Find (Cmd+F)** | Doesn't work in alt-screen | Works natively |
| **Warp AI block recognition** | Doesn't work in alt-screen | Works natively |
| **OSC8 hyperlinks** | Partial | Full |
| **tmux / screen compat** | Flaky redraw in alt-screen | Clean |
| **`/scroll` slash command** | Works | Removed (terminal native supersedes) |
| **Shift+↑/↓, PageUp/PageDown scroll** | Works | Removed (terminal native supersedes) |
| **History recall** | `Ctrl+↑/↓` (added in v1.23.1) | `Ctrl+↑/↓` (preserved) |
| **Submit key** | `Enter` | `Enter` (unchanged) |
| **Newline key** | `Shift+Enter` | `Shift+Enter` (unchanged) |
| **Slash popover** | Textual dropdown widget | prompt_toolkit CompletionsMenu Float |
| **Quick Open** | 2-column Textual screen (list + preview) | `radiolist_dialog` + auto-preview-first-20-lines to scrollback |
| **Marketplace** | Textual screen with plugin cards | `radiolist_dialog` + metadata `print_panel` + install confirm |
| **Settings modal** | Textual screen | `$EDITOR` opens `~/.llmcode/config.toml` directly |
| **Image paste** | ASCII art preview in chat view | Metadata-only dim line (`[image: WxH type, size]`) |
| **Vim mode default** | off (manual `/vim` toggle) | off (manual `/vim` toggle), but uses prompt_toolkit's built-in vim |
| **Voice hotkey** | `Ctrl+G` / `Ctrl+Space` | `Ctrl+G` / `Ctrl+Space` (preserved) |
| **Voice VAD** | v1.23.0 speech-gate | Same (unchanged) |

### 7.2 Removed slash commands

- `/scroll` — terminal native scrollback
- `/marketplace browse` — replaced by `/marketplace list`
- `/plugin browse` — replaced by `/plugin list`
- `/settings` (modal) — replaced by `/settings edit` opening `$EDITOR` on config file

Total: 62 → 58 slash commands at v2.0.0.

### 7.3 Session / config compatibility

- All existing v1.x session checkpoint files load unchanged in v2.0.0.
- Prompt history file (`~/.llmcode/prompt_history.txt`) format unchanged.
- Config file (`~/.llmcode/config.toml`) schema unchanged.
- New optional config key `view_backend` (future-proofing for v2.1+); defaults to `"repl"`, ignored in v2.0.0 since only one backend exists.

---

## 8. Migration Plan

### 8.1 Branch strategy

All v2.0.0 work on `feat/repl-mode` branch off `main` (at v1.23.1). Daily rebase onto main to pick up any hotfixes. Single-merge into main at release time.

### 8.2 Milestones (M1–M14)

**M0 — Proof of concept** *(added as a risk mitigation per Section 10.1)*
- 1-day spike validating that Rich `Live` + prompt_toolkit `Application` (full_screen=False) can coexist in Warp, iTerm2, and tmux
- Deliverable: a ~300 LOC throwaway prototype that shows a fake streaming response above a faked status line + input area, with no redraw corruption on resize
- Gate: if M0 fails, reconsider architecture before M1–M14 start

**M1 — Protocol base**
- `llm_code/view/__init__.py`, `base.py`, `types.py`, `ADDING_A_BACKEND.md`
- `tests/test_view/test_protocol_conformance.py` (25 tests)

**M2 — REPLPilot test abstraction**
- `tests/test_view/conftest.py` — `repl_pilot` fixture wrapping PT test session + Rich capture + dispatcher access
- 3 meta-tests for the pilot itself

**M3 — ScreenCoordinator skeleton**
- `view/repl/coordinator.py`, `view/repl/backend.py` (stub)
- Empty status line + empty input area, no components
- 15 tests

**M4 — Input + slash popover**
- `components/input_area.py`, `components/slash_popover.py`, `keybindings.py`
- Transliterate `tests/test_tui/test_input_bar.py` → `tests/test_view/test_input_area.py` (40 tests) + 20 new
- Integrates history (relocated from `tui/prompt_history.py`)

**M5 — Status line**
- `components/status_line.py` + coordinator.update_status wiring
- Layout 1: `{model} · {cwd}({branch}) · {ctx}/{limit} tok · ${cost}`
- Voice-active line replacement
- Rate-limit warning ConditionalContainer
- 20 tests

**M6 — LiveResponseRegion (Strategy Z)**
- `components/live_response_region.py`
- Rich Live + commit cycle
- 25 tests (including streaming of sample Markdown fixtures)

**M7 — ToolEventRegion (Style R)**
- `components/tool_event_renderer.py`
- Inline summary + diff auto-expand + failure auto-expand
- 35 tests (including fixture tool calls for read_file, bash, edit_file, failing bash)

**M8 — Dialog popovers**
- `components/dialog_popover.py`
- show_confirm / show_select / show_text_input / show_checklist via PT Float overlays
- Transliterate `tests/test_tui/test_dialogs_textual.py` → `tests/test_view/test_dialog_popover.py` (45 tests)

**M9 — Voice overlay + end-to-end voice flow**
- `components/voice_overlay.py`
- Wire AudioRecorder → coordinator via `call_soon_threadsafe`
- Full Ctrl+G → lock input → VAD → STT → insert flow
- Transliterate `tests/test_e2e_tui/test_voice_flow.py` → `tests/test_e2e_repl/test_voice_flow.py` (15 transliterated + 15 new = 30 tests)

**M10 — Dispatcher relocation + 62 (→58) commands view-agnostic**
- Move `tui/command_dispatcher.py` → `view/dispatcher.py`
- Rewrite all 62 `_cmd_*` methods to use `self._view: ViewBackend` only; zero widget references
- Drop 4 commands (`/scroll`, `/marketplace browse`, `/plugin browse`, `/settings` modal)
- Transliterate `tests/test_tui/test_command_dispatcher.py` (150+ tests) → `tests/test_view/test_dispatcher.py`

**M11 — Entry point + tui/ package deletion**
- `llm_code/cli/tui_main.py` renamed to `main.py`
- Wire backend + dispatcher + runtime
- **Delete entire `llm_code/tui/` package** (20+ files, ~5000 lines)
- Relocate keepers (`prompt_history.py`, `keybindings.py`, `ansi_strip.py`, `double_press.py`) into `view/repl/` or `view/`
- Delete `tests/test_tui/` and `tests/test_e2e_tui/` (657 tests — already transliterated in M4–M10 where relevant, remainder genuinely Textual-specific and deleted)

**M12 — Pexpect E2E smoke suite**
- `tests/test_e2e_repl/test_smoke.py` with 20 pexpect tests: cold start, `/version`, `/quit`, Ctrl+D exit, slash popover, streaming, voice hotkey, dialog confirm, external editor, Ctrl+C cancel, session save/load, model switch, etc.

**M13 — Snapshot tests (20–30)**
- `tests/test_view/snapshots/` — status_line, tool event panels, dialog popover, live response, slash popover, help panel, error panel
- Golden files checked into git

**M14 — Docs + v2.0.0 release**
- Update `README.md` (screenshots, quick start)
- Write `CHANGELOG.md` v2.0.0 entry (breaking change notice + migration notes)
- Write `docs/migration-v2.md` for users with scripts / aliases
- Update `docs/architecture.md`
- Release: bump pyproject.toml → 2.0.0, tag, push, `gh release create v2.0.0`

### 8.3 Milestone dependency graph

```
M0 (proof of concept — gate)
│
├──> M1 (Protocol base) ──┬──> M2 (Pilot fixture)
                          │
                          └──> M3 (Coordinator skeleton)
                                 │
                                 ├──> M4 (Input + popover)     ┐
                                 ├──> M5 (Status line)         │ parallel
                                 ├──> M6 (LiveResponse)        │
                                 ├──> M7 (ToolEvent)           │
                                 ├──> M8 (Dialogs)             │
                                 └──> M9 (Voice)               ┘
                                          │
                                          v
                                    M10 (Dispatcher relocation)
                                          │
                                          v
                                    M11 (Entry point + tui/ deletion)
                                          │
                                          ├──> M12 (pexpect smoke)
                                          ├──> M13 (snapshots)
                                          └──> M14 (docs + release)
```

**M4–M9 are parallelizable** — they all depend only on M2 (pilot) and M3 (coordinator skeleton) and don't touch each other's files. M10 waits for all M4–M9 to hit usable state. M11 is a flag-day that deletes the old `tui/` package; must come after M10.

### 8.4 Pre-existing dependencies preserved

The following existing modules are reused unchanged by the REPL backend and are explicitly *not* part of the migration:

- `llm_code/runtime/*` — config, conversation, hooks, fallback, recovery, cost_tracker, etc.
- `llm_code/api/*` — ProviderClient, streaming, types
- `llm_code/tools/*` — tool registry, bash, edit, voice (AudioRecorder, VAD, STT)
- `llm_code/memory/*`
- `llm_code/recovery/*`
- `llm_code/cli/commands.py` — COMMAND_REGISTRY
- `llm_code/cli/oneshot.py` — `-q` / `-x` modes (separate entry, unchanged)
- `llm_code/cli/streaming.py` — IncrementalMarkdownRenderer (used by LiveResponseRegion internally)

---

## 9. Test Strategy

### 9.1 Test pyramid (target v2.0.0)

```
          E2E pexpect (~20 tests)         ← M12
         smoke: cold start → chat → quit

       Component tests (~200 tests)       ← M3–M9
      Rich capture + PT test session;
      StatusLine, InputArea, LiveResponse,
      ToolEvent, DialogPopover, SlashPopover

     Unit tests (~500 tests)              ← M1–M10 (sprinkled)
    Protocol conformance, dispatcher (58),
    history, keybindings, headless dialogs,
    voice flow, prompt/editor helpers

    Snapshot tests (20–30 goldens)        ← M13
   status_line, tool panels, dialogs,
   live response, slash popover
```

**Target:** ~750 new tests in `tests/test_view/` + `tests/test_e2e_repl/` (replacing ~657 Textual tests in `tests/test_tui/` + `tests/test_e2e_tui/` that get deleted in M11). Total repo test count moves from ~5354 at v1.23.x to approximately ~5447 at v2.0.0 (+93 net, −657 Textual, +750 new view tests). CI execution target for the view+e2e subset: ~120 seconds (vs ~64 seconds for current test_tui + test_e2e_tui subset); total CI stays under 10 minutes.

### 9.2 Progressive test transliteration (C2 method)

For each Textual test file we transliterate to a REPL equivalent in M4–M10:

```
Step 1: Read old test tests/test_tui/test_X.py
Step 2: Copy assert logic to tests/test_view/test_X.py
Step 3: Replace pilot_app fixture with repl_pilot fixture
Step 4: Map Textual widget access to coordinator state access:
          app.query_one(HeaderBar).model → pilot.status_line.model
          pilot.press("enter") → pilot.press("enter")  [unchanged]
          app.chat_view.entries → pilot.captured_renders
          app.query_one(InputBar).value → pilot.input.value
Step 5: Run new test, fix until green
Step 6: Delete old test
Step 7: commit message: "test(view): port test_X from TUI to REPL"
```

Average per test: 5–10 minutes. Of 657 Textual tests:
- ~400 transliterate directly (same logic, new fixture)
- ~150 become irrelevant (test Textual widget lifecycle; delete)
- ~100 require rewrite (view-specific behavior; redesign for REPL equivalent)

**Estimated transliteration budget:** 30–40 hours, distributed across M4–M10 alongside implementation work.

### 9.3 REPLPilot fixture surface

```python
# tests/test_view/conftest.py (excerpt)
@pytest.fixture
async def repl_pilot(tmp_path):
    """In-process REPL test pilot. Wraps prompt_toolkit test app session +
    Rich capture console + dispatcher with mocked runtime."""
    config = test_config(tmp_path)
    runtime = MockRuntime(config)
    backend = REPLBackend(config=config, runtime=runtime)
    dispatcher = CommandDispatcher(view=backend, runtime=runtime)
    backend.set_input_handler(dispatcher.run_turn)

    async with create_pipe_input() as input_pipe:
        pilot = _REPLPilot(backend, dispatcher, input_pipe)
        await pilot.start()
        try:
            yield pilot
        finally:
            await pilot.stop()


class _REPLPilot:
    """Test control surface for the REPL backend."""

    @property
    def status_line(self) -> StatusLine: ...
    @property
    def input(self) -> InputBuffer: ...
    @property
    def captured_renders(self) -> list[CapturedRender]:
        """All messages/events printed to the Rich console."""

    async def press(self, key: str) -> None: ...
    async def type(self, text: str) -> None: ...
    async def submit(self, text: str) -> None:
        await self.type(text)
        await self.press("enter")

    async def pause(self, duration: float = 0.01) -> None: ...
    async def feed_streaming_response(self, text: str) -> None:
        """Simulate an LLM streaming response without hitting the API."""
```

### 9.4 Snapshot policy

Limit snapshots to 20–30 visual-regression targets:

- `status_line_default.txt`
- `status_line_streaming.txt`
- `status_line_voice_recording.txt`
- `status_line_rate_limited.txt`
- `live_response_in_progress.txt`
- `live_response_committed.txt`
- `tool_event_read_file.txt`
- `tool_event_bash_success.txt`
- `tool_event_edit_file_with_diff.txt`
- `tool_event_bash_failure.txt`
- `slash_popover.txt`
- `dialog_confirm_normal.txt`
- `dialog_confirm_elevated.txt`
- `dialog_confirm_critical.txt`
- `dialog_select.txt`
- `dialog_checklist.txt`
- `help_panel.txt`
- `error_panel.txt`
- ... ~10 more

Snapshots regenerate via `pytest --snapshot-update`. Diff review is manual.

### 9.5 Voice testing

Uses existing `FakeAudioRecorder` mock pattern. No hardware in CI. Protocol conformance: any backend must invoke `voice_started` / `voice_progress` / `voice_stopped` at the right times given a scripted recorder event stream. REPL backend additionally asserts that status_line updates fire.

---

## 10. Risks and Mitigations

### 10.1 Top-tier risks

**R1. Rich Live + prompt_toolkit Application screen contention**
- **Probability:** medium. **Impact:** high.
- **Mitigation:** M0 proof-of-concept is a hard gate. ScreenCoordinator owns all stdout coordination through an asyncio.Lock. Test on Warp, iTerm2, and tmux in M0.

**R2. prompt_toolkit full_screen=False Application mode bugs**
- **Probability:** medium. **Impact:** high.
- **Mitigation:** Pin prompt_toolkit ≥ 3.0.47 (includes resize-redraw fixes). M0 validates. Fallback F1: demote to scroll-print mode (Strategy B from brainstorm) — keeps the Protocol + migration intact, changes only the REPL backend's streaming strategy.

**R3. Voice background thread + coordinator asyncio.Lock deadlock**
- **Probability:** medium. **Impact:** medium.
- **Mitigation:** strict lock ordering (coordinator lock always before recorder internals). M9 includes stress test of 1000 voice start/stop cycles.

### 10.2 Second-tier risks

**R4. Test transliteration overrun** (est 30–40h actual 60–80h)
- **Mitigation:** scan `tests/test_tui/` in M4 to identify purely Textual-lifecycle tests and delete outright. Preserve pexpect smoke as final safety net regardless of unit coverage.

**R5. Rich Markdown code block flicker** (unstyled → styled on close ``` arrival)
- **Mitigation:** accept. Drop `refresh_per_second` to 4Hz if flicker is annoying. Equivalent flicker exists in Claude Code.

**R6. Warp escape sequence compatibility**
- **Mitigation:** M0 Warp test. prompt_toolkit uses standard termcap sequences which Warp commits to supporting.

**R7. Vim mode UX regression** (prompt_toolkit vi vs current llmcode vim)
- **Mitigation:** document differences in migration guide. prompt_toolkit vim is reasonable-defaults vi; provide `/vim` toggle.

**R8. Quick Open preview pane loss**
- **Mitigation:** auto-preview-first-20-lines into scrollback as compensation. User can scroll/copy/find the preview after it lands.

**R9. CI time inflation** (est 64s → ~200s)
- **Mitigation:** accept. Parallel pexpect if needed.

**R10. External editor ($EDITOR) transition flicker**
- **Mitigation:** accept brief redraw artifact. vim takes full control, exits, PT app resumes.

### 10.3 Fallback plans

**Fallback F1** — if R1/R2 force us off Rich Live in M3 or M6:
- Downgrade to Strategy B scroll-print: `PromptSession` instead of full `Application`; no Live region; token-append streaming; status line printed once per turn header.
- Protocol + Dispatcher + tests ~90% reusable. Rewrite ~40% of `view/repl/` components.

**Fallback F2** — if R3 voice deadlock is unresolvable in M9:
- Downgrade voice to slash command only (`/voice on` / `/voice off`). Removes the Ctrl+G global hotkey.
- Downgrade from brainstorm A1 → A3. Document as known limitation for v2.0.0; revisit in v2.1.

**Fallback F3** — if M10 dispatcher relocation overruns:
- Keep `llm_code/tui/command_dispatcher.py` in place; update internal widget calls to ViewBackend calls without moving the file.
- Directory name is mildly misleading until v2.1 cleanup.

**Fallback F4** — if v2.0.0 hits a deadline:
- Ship M1–M11 + M14 only. Defer M12 (pexpect smoke) and M13 (snapshots) to v2.0.1.
- Release notes flag reduced E2E coverage.

### 10.4 Success criteria

**Hard gates (all must pass for v2.0.0 release):**
- ✅ M1–M11 milestone tests green
- ✅ M12 pexpect smoke minimum suite (cold start / quit / version / slash popover / Ctrl+D) green
- ✅ Manual verification: mouse drag-select copy works in Warp + iTerm2 + macOS Terminal
- ✅ Manual verification: terminal-native scroll-wheel works in Warp
- ✅ Manual verification: Ctrl+G voice hotkey + VAD + STT insertion works in Warp
- ✅ All 58 slash commands executable without error
- ✅ `pip install llmcode-cli==2.0.0` in a clean Python 3.10 venv runs `llmcode /version` without error
- ✅ No single file exceeds 800 lines (CLAUDE.md hard limit)
- ✅ Existing v1.x session checkpoints, prompt history, and config files load unchanged

**Soft gates (aim for, v2.0.1 if missed):**
- ⭕ Status + input area fits in 3 lines at 80×24
- ⭕ Streaming token → screen latency < 200ms
- ⭕ Cold start time < 2s
- ⭕ Voice hotkey → recording UI < 500ms
- ⭕ Snapshot tests: 0 false positives

**Post-release monitoring:**
- 📊 v1.23 → v2.0 upgrade issue count (first 7 days)
- 📊 GitHub issue count requesting Textual TUI restoration (if > 3, re-evaluate)

---

## 11. Open Questions (Resolved)

All open questions from brainstorm Section 5.2 are resolved:

- **U1 — Quick Open alternative:** `radiolist_dialog` + auto-preview-first-20-lines into scrollback.
- **U2 — Marketplace display:** `radiolist_dialog` list + metadata `print_panel` + install confirm via `show_confirm`.
- **U3 — Vim mode default:** off (user opts in with `/vim`).
- **U4 — Tool call spinner:** global spinner in status line only; no per-tool spinners.
- **U5 — Image paste fallback:** metadata-only dim line. Progressive enhancement to OSC 1337 in v2.x is out of scope.
- **U6 — `/scroll` slash command:** removed.
- **U7 — prompt_toolkit version:** `>=3.0.47`.
- **U8 — Dead slash commands:** cut `/scroll`, `/marketplace browse`, `/plugin browse`, `/settings` modal in M10. 62 → 58 commands.

---

## 12. Appendix: File Inventory

### 12.1 Files created (new)

```
llm_code/view/__init__.py                         (~20 lines)
llm_code/view/base.py                             (~250 lines)
llm_code/view/types.py                            (~150 lines)
llm_code/view/dispatcher.py                       (~1500 lines; relocated + rewritten from tui/command_dispatcher.py)
llm_code/view/headless.py                         (~400 lines; relocated from tui/dialogs/headless.py)
llm_code/view/ADDING_A_BACKEND.md                 (~150 lines)
llm_code/view/repl/__init__.py                    (~20 lines)
llm_code/view/repl/backend.py                     (~500 lines)
llm_code/view/repl/coordinator.py                 (~700 lines)
llm_code/view/repl/keybindings.py                 (~200 lines; merged with relocated tui/keybindings.py)
llm_code/view/repl/history.py                     (~150 lines; relocated from tui/prompt_history.py)
llm_code/view/repl/theme.py                       (~100 lines)
llm_code/view/repl/snapshots.py                   (~200 lines)
llm_code/view/repl/components/status_line.py      (~350 lines)
llm_code/view/repl/components/input_area.py       (~500 lines)
llm_code/view/repl/components/slash_popover.py    (~250 lines)
llm_code/view/repl/components/live_response_region.py  (~300 lines)
llm_code/view/repl/components/tool_event_renderer.py   (~500 lines)
llm_code/view/repl/components/dialog_popover.py   (~400 lines)
llm_code/view/repl/components/voice_overlay.py    (~200 lines)
llm_code/cli/main.py                              (renamed from tui_main.py; ~250 lines)

tests/test_view/conftest.py                       (~300 lines; REPLPilot fixture)
tests/test_view/test_protocol_conformance.py      (~500 lines)
tests/test_view/test_input_area.py                (~800 lines; transliterated)
tests/test_view/test_status_line.py               (~300 lines)
tests/test_view/test_live_response_region.py      (~400 lines)
tests/test_view/test_tool_event_renderer.py       (~600 lines)
tests/test_view/test_dialog_popover.py            (~700 lines; transliterated)
tests/test_view/test_slash_popover.py             (~250 lines)
tests/test_view/test_dispatcher.py                (~2000 lines; transliterated)
tests/test_view/snapshots/                        (~30 golden files)
tests/test_e2e_repl/test_smoke.py                 (~500 lines; pexpect)
tests/test_e2e_repl/test_voice_flow.py            (~400 lines; transliterated + new)

docs/migration-v2.md                              (~300 lines)
```

### 12.2 Files deleted

```
llm_code/tui/__init__.py
llm_code/tui/ansi_strip.py                 (relocated to view/repl/ utility)
llm_code/tui/app.py                        (1358 lines — deleted outright)
llm_code/tui/chat_view.py                  (deleted)
llm_code/tui/chat_widgets.py               (deleted)
llm_code/tui/command_dispatcher.py         (relocated to view/dispatcher.py)
llm_code/tui/compaction_label.py           (deleted)
llm_code/tui/diff_render.py                (deleted; logic absorbed into tool_event_renderer)
llm_code/tui/double_press.py               (relocated to view/repl/ utility)
llm_code/tui/header_bar.py                 (deleted; info folds into status_line)
llm_code/tui/input_bar.py                  (deleted; replaced by input_area.py)
llm_code/tui/keybindings.py                (relocated to view/repl/keybindings.py)
llm_code/tui/marketplace.py                (deleted; replaced by dispatcher flow)
llm_code/tui/mcp_approval.py               (deleted; replaced by dialog_popover)
llm_code/tui/prompt_history.py             (relocated to view/repl/history.py)
llm_code/tui/quick_open.py                 (deleted; replaced by dispatcher flow)
llm_code/tui/runtime_init.py               (deleted)
llm_code/tui/settings_modal.py             (deleted; $EDITOR replacement)
llm_code/tui/spinner_verbs.py              (deleted; Rich has its own)
llm_code/tui/status_bar.py                 (deleted; replaced by status_line.py)
llm_code/tui/stream_parser.py              (deleted; logic lives in cli/streaming.py)
llm_code/tui/streaming_handler.py          (463 lines — deleted)
llm_code/tui/theme.py                      (deleted)
llm_code/tui/themes.py                     (deleted)
llm_code/tui/tool_render.py                (deleted; absorbed into tool_event_renderer)
llm_code/tui/dialogs/__init__.py           (relocated to view/)
llm_code/tui/dialogs/api.py                (relocated to view/dialog_types.py)
llm_code/tui/dialogs/headless.py           (relocated to view/headless.py)
llm_code/tui/dialogs/scripted.py           (relocated to view/scripted.py)
llm_code/tui/dialogs/textual_backend.py    (deleted)

tests/test_tui/                            (all files deleted; transliterated versions live in tests/test_view/)
tests/test_e2e_tui/                        (all files deleted; transliterated versions live in tests/test_e2e_repl/)
```

**Total deletion:** ~5000 lines of code, ~28 files, ~657 tests.

### 12.3 Files untouched

- All of `llm_code/runtime/`, `llm_code/api/`, `llm_code/tools/`, `llm_code/memory/`, `llm_code/recovery/`, `llm_code/auth/`, `llm_code/sandbox/`, `llm_code/mcp/`, `llm_code/plugins/`, `llm_code/vim/`, etc.
- `llm_code/cli/commands.py`, `oneshot.py`, `streaming.py`, `render.py`, `image.py`, `status_line.py`, `updater.py`
- All existing tests outside `tests/test_tui/` and `tests/test_e2e_tui/`

---

## 13. Appendix: hermes-agent References

Files studied for Protocol signature design:

- `gateway/platforms/base.py:726` — `class BasePlatformAdapter(ABC)` — 1100+ lines, the full platform adapter contract
- `gateway/platforms/base.py:581` — `class MessageType(Enum)` — 13 message kinds
- `gateway/platforms/base.py:594` — `class ProcessingOutcome(Enum)`
- `gateway/platforms/base.py:603` — `@dataclass MessageEvent` — inbound message envelope
- `gateway/platforms/base.py:667` — `@dataclass SendResult` — outbound result envelope
- `gateway/platforms/ADDING_A_PLATFORM.md` — contributor doc; llmcode mirrors at `view/ADDING_A_BACKEND.md`

Concrete implementations surveyed for surface validation (sampled):

- `gateway/platforms/telegram.py`, `discord.py`, `slack.py`, `api_server.py`, `webhook.py`

---

**End of design spec.** Next step: writing-plans skill to generate per-milestone implementation plans.
