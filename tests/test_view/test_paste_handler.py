"""Tests for paste handler + bash mode + pasted-content registry (M15 B4/B5)."""
from __future__ import annotations

from prompt_toolkit.buffer import Buffer

from llm_code.view.repl.components.bash_mode import (
    BashModeState,
    is_bash_mode_input,
    strip_bash_prefix,
)
from llm_code.view.repl.components.paste_handler import PasteHandler
from llm_code.view.repl.components.pasted_ref import PastedContentRegistry


# === Pasted content registry ===


def test_registry_starts_empty() -> None:
    r = PastedContentRegistry()
    assert r.count() == 0


def test_register_text_assigns_monotonic_ids() -> None:
    r = PastedContentRegistry()
    a = r.register_text("a\nb")
    b = r.register_text("c")
    assert a.content_id == 1
    assert b.content_id == 2


def test_register_text_stores_line_count() -> None:
    r = PastedContentRegistry()
    pc = r.register_text("one\ntwo\nthree")
    assert pc.lines == 3


def test_register_text_marker_format() -> None:
    r = PastedContentRegistry()
    pc = r.register_text("a\nb\nc\nd\ne\nf")
    assert pc.marker == "[Pasted text #1, 6 lines]"


def test_register_image_sets_marker() -> None:
    r = PastedContentRegistry()
    pc = r.register_image(b"PNG-bytes")
    assert pc.marker == "[Image #1]"
    assert pc.image_bytes == b"PNG-bytes"


def test_expand_replaces_text_markers() -> None:
    r = PastedContentRegistry()
    pc = r.register_text("this\nis\nlong")
    before = f"Hello {pc.marker} world"
    after = r.expand(before)
    assert "this\nis\nlong" in after
    assert pc.marker not in after


# === Paste handler ===


def test_paste_handler_falls_back_when_no_clipboard() -> None:
    """With no pyperclip/PIL dependency results, paste is a safe no-op."""
    registry = PastedContentRegistry()
    buf = Buffer()
    handler = PasteHandler(registry)
    # Neither pyperclip nor PIL are guaranteed available in CI — the
    # call must complete without raising. It may or may not insert
    # content depending on the test environment.
    handler.paste(buf)


# === Bash mode ===


def test_bash_mode_detects_prefix() -> None:
    assert is_bash_mode_input("!ls -la")
    assert not is_bash_mode_input("ls -la")


def test_strip_bash_prefix() -> None:
    assert strip_bash_prefix("!git status") == "git status"
    assert strip_bash_prefix("git status") == "git status"


def test_bash_mode_state_tracks_buffer() -> None:
    state = BashModeState()
    state.set_from_buffer("hello")
    assert not state.active
    state.set_from_buffer("!ls")
    assert state.active
