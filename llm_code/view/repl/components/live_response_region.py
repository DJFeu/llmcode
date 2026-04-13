"""LiveResponseRegion — Strategy Z streaming rendering.

Each streaming assistant response gets its own LiveResponseRegion.
While the response is in progress:
  1. A rich.Live context manager drives an in-place refresh of the
     region at 10Hz, rendering the partial buffer as Markdown inside
     a bordered Panel with a cursor glyph.
  2. The Live region is ``transient=True`` so when it stops, the
     displayed region clears itself from the terminal (doesn't leave
     residue in scrollback).
  3. feed() appends new chunks to a buffer and triggers live.update().

On commit():
  - live.stop() clears the in-place region
  - The final buffer is rendered as plain Markdown (no Panel border)
    and written to Console, flowing into terminal scrollback as
    permanent, copyable, searchable output

On abort():
  - live.stop() clears the in-place region
  - Nothing is printed — the draft response is discarded

Coordination with ScreenCoordinator:
  - The region holds a reference to the coordinator for future
    extensions but does not currently need lock-based arbitration.
    Rich's Live has internal locking and PT's invalidate() is
    event-loop-thread-safe; the M0 PoC and the full M0-M9 integration
    suite showed no contention on Warp/iTerm2/tmux. The ``_screen_lock``
    introduced in M3 as an R1 mitigation was never actually acquired
    on any production code path and was removed in M9.5.
  - Only one LiveResponseRegion is active at a time per REPLBackend;
    starting a new one while one is active aborts the old one.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from rich.console import Console, RenderableType
from rich.live import Live
from rich.markdown import Markdown

from llm_code.view.repl import style
from llm_code.view.repl.components.markdown_render import render_markdown
from rich.panel import Panel
from rich.text import Text

from llm_code.view.types import Role

if TYPE_CHECKING:
    from llm_code.view.repl.coordinator import ScreenCoordinator


# Refresh rate. Too high = flicker and CPU burn; too low = perceptible lag.
# 10Hz matches claude-code and aider.
REFRESH_HZ = 10

# Block cursor glyph appended to the in-progress buffer for visual feedback
CURSOR_GLYPH = "▋"


class LiveResponseRegion:
    """A single streaming response region.

    Lifecycle:
        region = LiveResponseRegion(console=..., coordinator=..., role=Role.ASSISTANT)
        region.start()         # Live starts
        region.feed(chunk)     # updates in-place
        ...
        region.commit()        # finalize to scrollback
    """

    def __init__(
        self,
        *,
        console: Console,
        coordinator: "ScreenCoordinator",
        role: Role,
    ) -> None:
        self._console = console
        self._coordinator = coordinator
        self._role = role
        self._buffer = ""
        self._live: Optional[Live] = None
        self._committed = False
        self._aborted = False
        self._started = False

    def start(self) -> None:
        """Begin the in-place Live region. Idempotent."""
        if self._started:
            return
        self._started = True
        self._live = Live(
            self._render_in_progress(),
            console=self._console,
            refresh_per_second=REFRESH_HZ,
            transient=True,
            auto_refresh=True,
        )
        self._live.start()

    def feed(self, chunk: str) -> None:
        """Append a chunk and refresh the in-place render.

        No-op after commit()/abort(). Auto-starts if feed is called
        before an explicit start().
        """
        if self._committed or self._aborted:
            return
        if not self._started:
            self.start()
        self._buffer += chunk
        if self._live is not None:
            self._live.update(self._render_in_progress())

    def commit(self) -> None:
        """Stop the Live region and print final Markdown to scrollback.

        Idempotent after commit/abort. Empty buffer commits are valid
        but produce no visible output (the transient Live region is
        simply torn down).
        """
        if self._committed or self._aborted:
            return
        self._committed = True
        self._stop_live()
        # Print the final render as permanent scrollback content.
        if self._buffer.strip():
            self._console.print(self._render_final())

    def abort(self) -> None:
        """Stop the Live region without printing anything.

        Idempotent. Used for Ctrl+C cancellation or dispatcher errors
        where the in-progress draft should be discarded.
        """
        if self._committed or self._aborted:
            return
        self._aborted = True
        self._stop_live()

    @property
    def is_active(self) -> bool:
        return not (self._committed or self._aborted)

    @property
    def buffer(self) -> str:
        return self._buffer

    # === Internal ===

    def _stop_live(self) -> None:
        if self._live is not None:
            try:
                self._live.stop()
            except Exception:  # noqa: BLE001 — Rich may raise on thread shutdown
                pass
            self._live = None

    def _render_in_progress(self) -> RenderableType:
        """Render used WHILE streaming.

        Wrapped in a bordered Panel with a cursor glyph, so the user
        knows the response is still being written. Rich Markdown is
        re-parsed every frame — acceptable at 10Hz because the buffer
        is small during streaming.
        """
        body: RenderableType
        if self._buffer.strip():
            body = Markdown(self._buffer + CURSOR_GLYPH)
        else:
            body = Text(CURSOR_GLYPH, style="dim")
        role_label = self._role.value
        return Panel(
            body,
            border_style=style.palette.brand_accent,
            title=f"[{style.palette.hint_fg}]{role_label}[/]",
            title_align="left",
        )

    def _render_final(self) -> RenderableType:
        """Render used WHEN committing to scrollback.

        M15: uses the ``● `` bullet prefix (assistant_text) and
        Rich Markdown with lexer-detected code fences. No panel
        border — clean output that looks like Claude Code's
        assistant messages.
        """
        from rich.console import Group
        from rich.text import Text
        # Leading bullet in brand accent
        bullet = Text()
        bullet.append("● ", style=f"bold {style.palette.assistant_bullet}")
        # Body as markdown with syntax highlighting
        md = render_markdown(self._buffer)
        return Group(bullet, md)
