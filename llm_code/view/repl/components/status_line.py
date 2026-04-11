"""StatusLine — bottom-of-screen 1-line status display (Layout 1).

Format: {model} · {cwd}({branch}) · {ctx_used}/{ctx_limit} tok · ${cost}

Three special modes that replace the default format entirely:

1. Voice recording — `🎙 0:02.3 · peak 0.42 · Ctrl+G stop` (dim red)
2. Streaming — appends `· ⠋ 1.2k tok` spinner on the right
3. Rate-limited — separate warning row shown above status line via
   ConditionalContainer (coordinator owns that row)

All three are controlled by the merged StatusUpdate state fed through
StatusLine.merge(). StatusLine itself never renders the rate-limit
row — that's a separate Window in the coordinator layout.
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional, Tuple

from prompt_toolkit.formatted_text import FormattedText

from llm_code.view.types import StatusUpdate


# Spinner animation frames for streaming state. 10 frames at 10Hz = 1-sec cycle.
SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

# Max model name width in the status line; longer names truncate with ellipsis
MAX_MODEL_WIDTH = 20

# Max cwd basename width
MAX_CWD_WIDTH = 20


def _shorten_model(model: str, max_width: int = MAX_MODEL_WIDTH) -> str:
    """Shorten a model name for display.

    Rules:
    - If a `/` provider prefix is present, drop it first
    - If the remainder is <= max_width chars: return unchanged
    - Otherwise truncate to ``max_width`` total chars with a trailing
      ``...`` (so the result is always <= max_width)
    """
    if "/" in model:
        model = model.rsplit("/", 1)[-1]
    if len(model) <= max_width:
        return model
    return model[: max_width - 3] + "..."


def _format_tokens(n: Optional[int]) -> str:
    """Format a token count compactly: 1234 -> '1.2k'; 123000 -> '123k'."""
    if n is None:
        return "?"
    if n < 1000:
        return str(n)
    if n < 10000:
        return f"{n / 1000:.1f}k"
    return f"{n // 1000}k"


def _format_cost(cost: Optional[float]) -> str:
    if cost is None or cost == 0:
        return "$0.00"
    if cost < 0.01:
        return f"${cost:.4f}"
    return f"${cost:.2f}"


class StatusLine:
    """Holds merged StatusUpdate state; renders FormattedText on demand."""

    def __init__(self) -> None:
        self._state = StatusUpdate()
        self._spinner_frame = 0

    def merge(self, update: StatusUpdate) -> None:
        """Apply a partial StatusUpdate — non-None fields overwrite current state."""
        for field_name in update.__dataclass_fields__:
            value = getattr(update, field_name)
            if value is None:
                continue
            # Boolean False is significant for streaming/voice clears
            if field_name in {"is_streaming", "voice_active"} and value is False:
                setattr(self._state, field_name, False)
                continue
            setattr(self._state, field_name, value)

    @property
    def state(self) -> StatusUpdate:
        return self._state

    def is_rate_limited(self) -> bool:
        return (
            self._state.rate_limit_until is not None
            and self._state.rate_limit_until > datetime.now()
        )

    def advance_spinner(self) -> None:
        """Cycle the streaming spinner. Called by coordinator on each redraw."""
        self._spinner_frame = (self._spinner_frame + 1) % len(SPINNER_FRAMES)

    def render_formatted_text(self) -> FormattedText:
        """Return a prompt_toolkit FormattedText for the status line."""
        if self._state.voice_active:
            return self._render_voice_mode()
        return self._render_default()

    def render_rate_limit_warning(self) -> FormattedText:
        """Return the rate-limit warning row. Coordinator uses this in a
        ConditionalContainer."""
        if not self.is_rate_limited():
            return FormattedText([])
        retry_at = (
            self._state.rate_limit_until.strftime("%H:%M:%S")
            if self._state.rate_limit_until
            else "?"
        )
        reqs = self._state.rate_limit_reqs_left
        reqs_str = f"· {reqs} reqs left" if reqs is not None else ""
        return FormattedText([
            ("fg:ansired bold", f" ⚠ rate limited · retry {retry_at} {reqs_str} "),
        ])

    def _render_default(self) -> FormattedText:
        s = self._state
        model = _shorten_model(s.model) if s.model else "?"
        branch = s.branch or "-"
        cwd = s.cwd or "?"
        ctx_used = _format_tokens(s.context_used_tokens)
        ctx_limit = _format_tokens(s.context_limit_tokens)
        cost = _format_cost(s.cost_usd)

        parts: List[Tuple[str, str]] = [
            ("class:status", f" {model} · {cwd}({branch}) · "),
            ("class:status", f"{ctx_used}/{ctx_limit} tok · "),
            ("class:status", f"{cost} "),
        ]

        if s.permission_mode and s.permission_mode not in {"normal", "default", None}:
            parts.insert(
                1, ("class:status.mode bold", f"[{s.permission_mode}] ")
            )

        if s.is_streaming:
            frame = SPINNER_FRAMES[self._spinner_frame]
            token_str = (
                f" · {frame} {_format_tokens(s.streaming_token_count)} tok"
                if s.streaming_token_count is not None
                else f" · {frame} thinking..."
            )
            parts.append(("class:status.spinner", token_str))

        return FormattedText(parts)

    def _render_voice_mode(self) -> FormattedText:
        s = self._state
        secs = s.voice_seconds or 0.0
        mins = int(secs // 60)
        rem = secs - mins * 60
        time_str = f"{mins}:{rem:04.1f}"
        peak = s.voice_peak or 0.0
        return FormattedText([
            ("fg:ansired bold", f" 🎙 {time_str} · peak {peak:.2f} · Ctrl+G stop "),
        ])
