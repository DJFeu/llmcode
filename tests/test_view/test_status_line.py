"""Tests for StatusLine component."""
from __future__ import annotations

from datetime import datetime, timedelta

from prompt_toolkit.formatted_text import FormattedText

from llm_code.view.repl.components.status_line import (
    SPINNER_FRAMES,
    StatusLine,
    _format_cost,
    _format_tokens,
    _shorten_model,
)
from llm_code.view.types import StatusUpdate


def _text(ft: FormattedText) -> str:
    """Flatten FormattedText to plain string."""
    return "".join(segment[1] for segment in ft)


# === Formatting helpers ===


def test_shorten_model_short():
    assert _shorten_model("Q3.5-122B") == "Q3.5-122B"


def test_shorten_model_drops_provider_prefix():
    # Input after dropping 'nous/' is 32 chars. Truncate to max_width=20:
    # keep [:17] + '...' = "Qwen3.5-122B-A18B..."
    assert (
        _shorten_model("nous/Qwen3.5-122B-A18B-Int4-AutoRound")
        == "Qwen3.5-122B-A18B..."
    )


def test_shorten_model_truncates_long():
    result = _shorten_model("a-very-long-model-name-that-exceeds-limit")
    assert result.endswith("...")
    assert len(result) == 20


def test_shorten_model_with_provider_prefix_fits():
    """After dropping the provider prefix, a short name stays intact."""
    assert _shorten_model("anthropic/claude-opus") == "claude-opus"


def test_format_tokens_small():
    assert _format_tokens(500) == "500"
    assert _format_tokens(0) == "0"
    assert _format_tokens(None) == "-"


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


def test_spinner_wraps_around():
    """Spinner index cycles modulo len(SPINNER_FRAMES)."""
    sl = StatusLine()
    for _ in range(len(SPINNER_FRAMES)):
        sl.advance_spinner()
    assert sl._spinner_frame == 0


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


def test_voice_mode_clear_returns_to_default():
    """Setting voice_active=False returns to the default render."""
    sl = StatusLine()
    sl.merge(StatusUpdate(model="M", voice_active=True))
    assert "🎙" in _text(sl.render_formatted_text())
    sl.merge(StatusUpdate(voice_active=False))
    text = _text(sl.render_formatted_text())
    assert "🎙" not in text
    assert "M" in text


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


# === M6 trace glyph ===


def test_trace_glyph_absent_by_default(monkeypatch):
    """Without a trace URL or active OTel span, no glyph appears."""
    from llm_code.view.repl.components import status_line as sl_mod

    monkeypatch.delenv("LLMCODE_TRACE_URL", raising=False)
    # With no active OTel span, tracing_link returns None.
    sl = StatusLine()
    sl.merge(StatusUpdate(model="m", cwd="~", branch="main"))
    text = _text(sl.render_formatted_text())
    assert sl_mod.TRACE_GLYPH not in text


def test_trace_glyph_shown_with_override_env(monkeypatch):
    """LLMCODE_TRACE_URL override forces the glyph on."""
    from llm_code.view.repl.components import status_line as sl_mod

    monkeypatch.setenv("LLMCODE_TRACE_URL", "https://example.com/trace/abc")
    sl = StatusLine()
    sl.merge(StatusUpdate(model="m", cwd="~", branch="main"))
    text = _text(sl.render_formatted_text())
    assert sl_mod.TRACE_GLYPH in text
    # OSC 8 bracket sequence is present when the glyph renders.
    assert "\x1b]8;;" in text


def test_format_trace_glyph_wraps_in_osc8():
    from llm_code.view.repl.components.status_line import (
        TRACE_GLYPH,
        format_trace_glyph,
    )

    result = format_trace_glyph("https://example.com/trace/abc")
    assert TRACE_GLYPH in result
    assert result.startswith("\x1b]8;;https://example.com/trace/abc\x1b\\")
    assert result.endswith("\x1b]8;;\x1b\\")


def test_format_trace_glyph_empty_when_no_url():
    from llm_code.view.repl.components.status_line import format_trace_glyph

    assert format_trace_glyph(None) == ""
    assert format_trace_glyph("") == ""


def test_tracing_link_override_wins(monkeypatch):
    from llm_code.view.repl.components.status_line import tracing_link

    monkeypatch.setenv("LLMCODE_TRACE_URL", "https://override.example/x")
    assert tracing_link() == "https://override.example/x"
