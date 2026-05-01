"""StatusLine — bottom-of-screen 1-line status display (Layout 1).

Format: {model} · {cwd}({branch}) · {ctx_used}/{ctx_limit} tok · ${cost}

Three special modes that replace the default format entirely:

1. Voice recording — `🎙 0:02.3 · peak 0.42 · Ctrl+G stop` (dim red)
2. Streaming — appends `· ⠋ 1.2k tok` spinner on the right
3. Rate-limited — separate warning row shown above status line via
   ConditionalContainer (coordinator owns that row)

M6 observability: when a trace is active (see
:func:`tracing_link` below), a ``⎈`` glyph with an OSC 8 hyperlink is
appended to the default render. Clicking the glyph in a compliant
terminal opens the matching trace in Langfuse / Jaeger.

All three are controlled by the merged StatusUpdate state fed through
StatusLine.merge(). StatusLine itself never renders the rate-limit
row — that's a separate Window in the coordinator layout.
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional, Tuple

from prompt_toolkit.formatted_text import FormattedText

from llm_code.view.repl.components.context_meter import render_context_meter
from llm_code.view.types import StatusUpdate


# Spinner animation frames for streaming state. 10 frames at 10Hz = 1-sec cycle.
SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

# Max model name width in the status line; longer names truncate with ellipsis
MAX_MODEL_WIDTH = 20

# Max cwd display width in the status line. Balances readability
# (show enough of the path to be useful) against line wrap when the
# user lives deep in a temp directory or long-named project tree.
# pytest-of-<user>/pytest-<n>/... paths reach ~80 chars; we shrink
# those to ``.../basename`` so the rest of the status line survives.
MAX_CWD_WIDTH = 30


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


def _shorten_cwd(cwd: Optional[str], max_width: int = MAX_CWD_WIDTH) -> str:
    """Shorten a cwd for display.

    Rules:

    - Collapse a home-prefixed path to ``~/…`` (matches the common
      shell-prompt convention).
    - If still ≤ ``max_width``: return unchanged.
    - Otherwise return ``.../<basename>`` (or the basename alone when
      it would overflow ``max_width`` by itself — truncated with ``...``).
    - Returns ``"?"`` for ``None`` / empty so the render never breaks.
    """
    if not cwd:
        return "?"
    from pathlib import Path

    home = str(Path.home())
    if cwd == home:
        return "~"
    if cwd.startswith(home + "/"):
        cwd = "~" + cwd[len(home):]
    if len(cwd) <= max_width:
        return cwd
    basename = cwd.rsplit("/", 1)[-1] or cwd
    prefix_budget = max_width - len(basename) - 4  # ".../" + basename
    if prefix_budget >= 0 and len(basename) <= max_width - 4:
        return f".../{basename}"
    # basename alone overflows — truncate with trailing ellipsis.
    if max_width < 4:
        return basename[:max_width]
    return basename[: max_width - 3] + "..."


def _format_tokens(n: Optional[int]) -> str:
    """Format a token count compactly: 1234 -> '1.2k'; 123000 -> '123k'."""
    if n is None:
        return "-"
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


# OSC 8 hyperlink escape sequences — wrap text so a compliant terminal
# emulator renders it as a clickable link without changing the visible
# characters. Format: ESC ] 8 ;; URL ST text ESC ] 8 ;; ST
_OSC8_START = "\x1b]8;;{url}\x1b\\"
_OSC8_END = "\x1b]8;;\x1b\\"

TRACE_GLYPH = "\u2388"  # ⎈


def tracing_link() -> Optional[str]:
    """Return the URL for the currently-active trace, or ``None``.

    The URL is picked from the following in order:

    * Explicit ``LLMCODE_TRACE_URL`` env var (set by the caller; useful
      for CI/custom integrations).
    * Langfuse ``{host}/trace/{trace_id}`` when a Langfuse exporter is
      configured and ``LANGFUSE_HOST`` is set.
    * ``None`` otherwise — caller should skip the glyph.

    The OTel trace id is read via the optional ``opentelemetry`` dep;
    when it's not installed this function returns ``None`` so the
    status line gracefully omits the glyph.
    """
    import os

    override = os.environ.get("LLMCODE_TRACE_URL")
    if override:
        return override

    try:
        from opentelemetry import trace as _otel_trace  # type: ignore[import-not-found]
    except ImportError:
        return None

    span = _otel_trace.get_current_span()
    if span is None:
        return None
    ctx = span.get_span_context()
    if not getattr(ctx, "trace_id", 0):
        return None
    trace_hex = format(ctx.trace_id, "032x")

    host = os.environ.get("LANGFUSE_HOST") or "https://cloud.langfuse.com"
    return f"{host.rstrip('/')}/trace/{trace_hex}"


def format_trace_glyph(url: Optional[str]) -> str:
    """Return the OSC 8-wrapped glyph, or empty string when url is ``None``."""
    if not url:
        return ""
    return f"{_OSC8_START.format(url=url)}{TRACE_GLYPH}{_OSC8_END}"


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
        cwd = _shorten_cwd(s.cwd)
        ctx_used = _format_tokens(s.context_used_tokens)
        ctx_limit = _format_tokens(s.context_limit_tokens)
        cost = _format_cost(s.cost_usd)

        parts: List[Tuple[str, str]] = [("class:status", f" {model}")]

        if s.permission_mode and s.permission_mode not in {"normal", "default", None}:
            parts.append(("class:status.mode bold", f" · mode:{s.permission_mode}"))

        parts.append(("class:status", f" · {cwd}({branch}) · "))
        if s.context_used_tokens is not None and s.context_limit_tokens is not None:
            parts.extend(render_context_meter(
                s.context_used_tokens,
                s.context_limit_tokens,
                compact=True,
            ))
            parts.append(("class:status", " · "))
        else:
            parts.append(("class:status", f"{ctx_used}/{ctx_limit} tok · "))
        parts.append(("class:status", cost))

        if s.is_streaming:
            frame = SPINNER_FRAMES[self._spinner_frame]
            token_str = (
                f" · {frame} {_format_tokens(s.streaming_token_count)} tok"
                if s.streaming_token_count is not None
                else f" · {frame} thinking..."
            )
            parts.append(("class:status.spinner", token_str))

        # M6: trace glyph with OSC 8 hyperlink, appended when a trace is
        # active. The URL lookup is defensive — any exception falls
        # through to "no glyph" so the status line never breaks.
        try:
            url = tracing_link()
        except Exception:
            url = None
        glyph = format_trace_glyph(url)
        if glyph:
            parts.append(("class:status.trace", f" · {glyph}"))

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
