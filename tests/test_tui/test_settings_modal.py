"""Tests for the read-only settings panel section builder."""
from __future__ import annotations

from llm_code.tui.settings_modal import build_settings_sections, render_sections_text


class _Cfg:
    provider = "openai"
    model = "qwen3.5-coder"
    max_tokens = 4096
    temperature = 0.2
    thinking_enabled = True


class _Tracker:
    total_input_tokens = 1234
    total_output_tokens = 5678
    total_cost_usd = 0.0123


class _KB:
    def get_all_bindings(self):
        return {"submit": "enter", "cancel": "escape"}


class _Runtime:
    model = "qwen3.5-coder"
    permission_mode = "build"
    plan_mode = False
    config = _Cfg()
    cost_tracker = _Tracker()
    keybindings = _KB()
    active_skills = ["python-patterns", "git-workflow"]


def test_sections_built():
    secs = build_settings_sections(_Runtime())
    titles = [s.title for s in secs]
    assert titles == ["Status", "Config", "Usage", "Keybindings", "Skills"]


def test_status_fields():
    secs = build_settings_sections(_Runtime())
    status = secs[0]
    keys = dict(status.fields)
    assert keys["Model"] == "qwen3.5-coder"
    assert keys["Permission mode"] == "build"


def test_no_crash_on_missing_fields():
    class Empty:
        pass
    secs = build_settings_sections(Empty())
    assert len(secs) == 5  # all sections still built


def test_render_sections_text_includes_titles():
    secs = build_settings_sections(_Runtime())
    text = render_sections_text(secs)
    assert "── Status ──" in text
    assert "── Skills ──" in text
    assert "python-patterns" in text


def test_keybindings_section_populated():
    secs = build_settings_sections(_Runtime())
    kb = next(s for s in secs if s.title == "Keybindings")
    keys = dict(kb.fields)
    assert keys.get("submit") == "enter"
