"""Integration tests for VimEngine."""
from __future__ import annotations

from llm_code.vim.engine import VimEngine
from llm_code.vim.types import VimMode


class TestVimEngine:
    def test_starts_in_insert_mode(self):
        engine = VimEngine("hello")
        assert engine.mode == VimMode.INSERT

    def test_esc_to_normal(self):
        engine = VimEngine("hello")
        engine.feed_key("\x1b")
        assert engine.mode == VimMode.NORMAL

    def test_typing_in_insert(self):
        engine = VimEngine("")
        engine.feed_key("h")
        engine.feed_key("i")
        assert engine.buffer == "hi"
        assert engine.cursor == 2

    def test_normal_mode_navigation(self):
        engine = VimEngine("hello world")
        engine.feed_key("\x1b")
        engine.feed_key("0")
        assert engine.cursor == 0
        engine.feed_key("w")
        assert engine.cursor == 6

    def test_delete_word(self):
        engine = VimEngine("hello world")
        engine.feed_key("\x1b")
        engine.feed_key("0")
        engine.feed_key("d")
        engine.feed_key("w")
        assert engine.buffer == "world"

    def test_full_editing_sequence(self):
        engine = VimEngine("hello world")
        engine.feed_key("\x1b")   # NORMAL
        engine.feed_key("0")     # go to start
        engine.feed_key("c")     # change
        engine.feed_key("w")     # word
        assert engine.mode == VimMode.INSERT
        # Type replacement
        engine.feed_key("g")
        engine.feed_key("o")
        engine.feed_key("o")
        engine.feed_key("d")
        engine.feed_key("\x1b")  # back to NORMAL
        assert engine.buffer == "good world"

    def test_feed_keys_helper(self):
        engine = VimEngine("hello")
        engine.feed_keys("\x1b0x")
        assert engine.buffer == "ello"

    def test_snapshot_returns_state(self):
        engine = VimEngine("hello")
        snap = engine.snapshot()
        assert snap.buffer == "hello"
        assert snap.mode == VimMode.INSERT
