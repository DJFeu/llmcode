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
