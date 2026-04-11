# M5 — Status Line

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans.

**Goal:** Implement the Layout-1 status line from spec §6.2: `{model} · {cwd}({branch}) · {ctx_used}/{ctx_limit} tok · ${cost}`, with three special-state modes (streaming spinner, voice recording, rate-limited) and a `ConditionalContainer` rate-limit warning row that auto-shows/hides. Wire it into `ScreenCoordinator.update_status()`.

**Architecture:** `StatusLine` component holds the current merged `StatusUpdate` state and exposes `render_formatted_text()` returning PT `FormattedText`. Coordinator's existing placeholder `_status_text()` function is replaced with `self._status_line.render_formatted_text`. Rate-limit warning is a separate `Window` wrapped in `ConditionalContainer`, inserted above the status line in the layout.

**Tech Stack:** prompt_toolkit `FormattedText`, `FormattedTextControl`, `ConditionalContainer`, `Filter`, Rich (for escape/style strings).

**Spec reference:** §4 v1.23 to v2.0 diff, §6.2 bottom layout, §7.1 user-facing behavior.

**Dependencies:** M3 complete (coordinator skeleton), M4 complete (coordinator uses `FloatContainer`). Can run in parallel with M6/M7/M8/M9.

---

## File Structure

- Create: `llm_code/view/repl/components/status_line.py` — `StatusLine` class (~300 lines)
- Modify: `llm_code/view/repl/coordinator.py` — replace placeholder `_status_text` with `self._status_line`, add rate-limit row
- Create: `tests/test_view/test_status_line.py` — ~25 tests, ~400 lines

---

## Tasks

### Task 5.1: Write StatusLine component

**Files:** Create `llm_code/view/repl/components/status_line.py`

- [ ] **Step 1: Write the class.**

```python
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

from dataclasses import replace
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
    - If ≤ max_width chars: return unchanged
    - If the name contains a `/` provider prefix: drop it
    - Else truncate with a trailing ellipsis (...)
    """
    if "/" in model:
        model = model.rsplit("/", 1)[-1]
    if len(model) <= max_width:
        return model
    return model[: max_width - 3] + "..."


def _format_tokens(n: Optional[int]) -> str:
    """Format a token count compactly: 1234 → '1.2k'; 123000 → '123k'."""
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
        retry_at = self._state.rate_limit_until.strftime("%H:%M:%S") if self._state.rate_limit_until else "?"
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
```

- [ ] **Step 2: Commit** — `git add llm_code/view/repl/components/status_line.py && git commit -m "feat(view): StatusLine component (Layout 1)"`

### Task 5.2: Wire StatusLine into coordinator

**Files:** Modify `llm_code/view/repl/coordinator.py`

- [ ] **Step 1: Edit coordinator.**

```python
# Add import
from llm_code.view.repl.components.status_line import StatusLine
from prompt_toolkit.filters import Condition

# In __init__, replace:
#   self._current_status = StatusUpdate()
# with:
self._status_line = StatusLine()

# Add a @property current_status that forwards to self._status_line.state:
@property
def current_status(self) -> StatusUpdate:
    return self._status_line.state

# Replace the _status_text method with:
def _status_text(self) -> FormattedText:
    self._status_line.advance_spinner()
    return self._status_line.render_formatted_text()

# Rewrite update_status:
def update_status(self, status: StatusUpdate) -> None:
    self._status_line.merge(status)
    if self._app is not None and self._app.is_running:
        self._app.invalidate()

# In _build_layout, insert the rate-limit warning row above the status line:
def _build_layout(self) -> Layout:
    rate_limit_warning = Window(
        FormattedTextControl(self._status_line.render_rate_limit_warning),
        height=1,
        style="class:rate-limit",
    )
    rate_limit_container = ConditionalContainer(
        content=rate_limit_warning,
        filter=Condition(self._status_line.is_rate_limited),
    )
    status_window = Window(
        FormattedTextControl(self._status_text),
        height=1,
        style="class:status",
    )
    input_window = self._input_area.build_window()
    popover_float = self._input_area.build_popover_float()
    return Layout(
        FloatContainer(
            content=HSplit([
                rate_limit_container,
                status_window,
                input_window,
            ]),
            floats=[popover_float],
        )
    )
```

- [ ] **Step 2: Update style Dict to include new classes.**

```python
def _build_style(self) -> Style:
    return Style.from_dict({
        "status": "reverse",
        "status.mode": "reverse fg:ansiyellow",
        "status.spinner": "reverse fg:ansicyan",
        "rate-limit": "fg:ansired reverse",
        "input": "",
    })
```

- [ ] **Step 3: Run coordinator + pilot tests** — `pytest tests/test_view/test_coordinator.py tests/test_view/test_pilot.py -v` → all pass.
- [ ] **Step 4: Commit** — `git commit -am "feat(view): coordinator uses StatusLine + rate-limit ConditionalContainer"`

### Task 5.3: Write StatusLine tests

**Files:** Create `tests/test_view/test_status_line.py`

- [ ] **Step 1: Write tests.**

```python
"""Tests for StatusLine component."""
import pytest
from datetime import datetime, timedelta
from prompt_toolkit.formatted_text import FormattedText

from llm_code.view.repl.components.status_line import (
    StatusLine, _shorten_model, _format_tokens, _format_cost, SPINNER_FRAMES,
)
from llm_code.view.types import StatusUpdate


def _text(ft: FormattedText) -> str:
    """Flatten FormattedText to plain string."""
    return "".join(segment[1] for segment in ft)


# === Formatting helpers ===

def test_shorten_model_short():
    assert _shorten_model("Q3.5-122B") == "Q3.5-122B"

def test_shorten_model_drops_provider_prefix():
    assert _shorten_model("nous/Qwen3.5-122B-A18B-Int4-AutoRound") == "Qwen3.5-122B-A18B-In..."

def test_shorten_model_truncates_long():
    assert _shorten_model("a-very-long-model-name-that-exceeds-limit").endswith("...")
    assert len(_shorten_model("a-very-long-model-name-that-exceeds-limit")) == 20

def test_format_tokens_small():
    assert _format_tokens(500) == "500"
    assert _format_tokens(0) == "0"
    assert _format_tokens(None) == "?"

def test_format_tokens_thousand():
    assert _format_tokens(1200) == "1.2k"
    assert _format_tokens(9999) == "10.0k"

def test_format_tokens_ten_thousand():
    assert _format_tokens(16400) == "16k"
    assert _format_tokens(128000) == "128k"

def test_format_cost_zero():
    assert _format_cost(0) == "$0.00"
    assert _format_cost(None) == "$0.00"

def test_format_cost_tiny():
    assert _format_cost(0.0052) == "$0.0052"

def test_format_cost_normal():
    assert _format_cost(1.23) == "$1.23"

# === StatusLine state ===

def test_initial_state_is_empty():
    sl = StatusLine()
    assert sl.state.model is None
    assert sl.state.cost_usd is None

def test_merge_applies_non_none_fields():
    sl = StatusLine()
    sl.merge(StatusUpdate(model="M1", cost_usd=0.05))
    assert sl.state.model == "M1"
    assert sl.state.cost_usd == 0.05

def test_merge_preserves_unset_fields():
    sl = StatusLine()
    sl.merge(StatusUpdate(model="M1"))
    sl.merge(StatusUpdate(cost_usd=0.05))
    assert sl.state.model == "M1"
    assert sl.state.cost_usd == 0.05

def test_merge_overwrites_with_new_value():
    sl = StatusLine()
    sl.merge(StatusUpdate(cost_usd=0.05))
    sl.merge(StatusUpdate(cost_usd=0.10))
    assert sl.state.cost_usd == 0.10

def test_merge_clears_streaming_with_false():
    sl = StatusLine()
    sl.merge(StatusUpdate(is_streaming=True))
    assert sl.state.is_streaming is True
    sl.merge(StatusUpdate(is_streaming=False))
    assert sl.state.is_streaming is False

# === Default render ===

def test_default_render_shows_all_fields():
    sl = StatusLine()
    sl.merge(StatusUpdate(
        model="Q3.5-122B",
        cwd="llm-code",
        branch="main",
        context_used_tokens=16400,
        context_limit_tokens=128000,
        cost_usd=0.0,
    ))
    text = _text(sl.render_formatted_text())
    assert "Q3.5-122B" in text
    assert "llm-code" in text
    assert "main" in text
    assert "16k" in text
    assert "128k" in text
    assert "$0.00" in text

def test_default_render_without_branch():
    sl = StatusLine()
    sl.merge(StatusUpdate(model="M", cwd="repo"))
    text = _text(sl.render_formatted_text())
    assert "repo(-)" in text  # fallback branch

def test_permission_mode_shown_for_non_default():
    sl = StatusLine()
    sl.merge(StatusUpdate(model="M", permission_mode="plan"))
    text = _text(sl.render_formatted_text())
    assert "[plan]" in text

def test_permission_mode_hidden_for_normal():
    sl = StatusLine()
    sl.merge(StatusUpdate(model="M", permission_mode="normal"))
    text = _text(sl.render_formatted_text())
    assert "[normal]" not in text
    assert "[" not in text

# === Streaming mode ===

def test_streaming_shows_spinner():
    sl = StatusLine()
    sl.merge(StatusUpdate(model="M", is_streaming=True))
    text = _text(sl.render_formatted_text())
    # First frame (spinner_frame starts at 0)
    assert SPINNER_FRAMES[0] in text
    assert "thinking" in text.lower() or "tok" in text

def test_streaming_shows_token_count():
    sl = StatusLine()
    sl.merge(StatusUpdate(
        model="M", is_streaming=True, streaming_token_count=1234,
    ))
    text = _text(sl.render_formatted_text())
    assert "1.2k" in text

def test_spinner_advances():
    sl = StatusLine()
    sl.merge(StatusUpdate(is_streaming=True))
    frame1 = sl._spinner_frame
    sl.advance_spinner()
    frame2 = sl._spinner_frame
    assert frame1 != frame2

# === Voice mode (replaces entire line) ===

def test_voice_mode_replaces_default():
    sl = StatusLine()
    sl.merge(StatusUpdate(model="M", cost_usd=0.5))  # normal state
    sl.merge(StatusUpdate(
        voice_active=True, voice_seconds=2.3, voice_peak=0.42,
    ))
    text = _text(sl.render_formatted_text())
    assert "🎙" in text
    assert "0:02.3" in text
    assert "0.42" in text
    assert "Ctrl+G stop" in text
    # Normal fields are NOT shown in voice mode
    assert "M" not in text.split("🎙")[0]

def test_voice_mode_timer_format_minutes():
    sl = StatusLine()
    sl.merge(StatusUpdate(voice_active=True, voice_seconds=125.4))
    text = _text(sl.render_formatted_text())
    assert "2:05.4" in text

# === Rate limit warning ===

def test_rate_limit_warning_hidden_by_default():
    sl = StatusLine()
    assert sl.is_rate_limited() is False
    assert _text(sl.render_rate_limit_warning()) == ""

def test_rate_limit_warning_shown_when_active():
    sl = StatusLine()
    future = datetime.now() + timedelta(minutes=5)
    sl.merge(StatusUpdate(rate_limit_until=future, rate_limit_reqs_left=3))
    assert sl.is_rate_limited() is True
    text = _text(sl.render_rate_limit_warning())
    assert "rate limited" in text
    assert "3 reqs left" in text

def test_rate_limit_warning_expired_hidden():
    sl = StatusLine()
    past = datetime.now() - timedelta(minutes=1)
    sl.merge(StatusUpdate(rate_limit_until=past))
    assert sl.is_rate_limited() is False
```

- [ ] **Step 2: Run tests** — `pytest tests/test_view/test_status_line.py -v` → ~25 pass.
- [ ] **Step 3: Commit** — `git add tests/test_view/test_status_line.py && git commit -m "test(view): StatusLine rendering + rate-limit warning"`

### Task 5.4: Full verification

- [ ] **Step 1: Run all view tests** — `pytest tests/test_view/ -v` → 0 failures.
- [ ] **Step 2: Push** — `git push origin feat/repl-mode`

---

## Milestone completion criteria

- ✅ `StatusLine` class with merge/render/advance_spinner/is_rate_limited
- ✅ Coordinator's `_build_layout` includes rate-limit `ConditionalContainer` row above status
- ✅ `update_status()` merges into StatusLine and invalidates app
- ✅ ~25 tests green in `test_status_line.py`
- ✅ All existing view tests still pass

## Estimated effort: ~2 hours

## Next milestone: M6 — LiveResponseRegion (`m6-live-response.md`)
