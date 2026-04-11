"""Tests for LiveResponseRegion — Strategy Z streaming rendering."""
from __future__ import annotations

import io

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

from llm_code.view.repl.components.live_response_region import (
    CURSOR_GLYPH,
    LiveResponseRegion,
)
from llm_code.view.types import Role


def _make(role: Role = Role.ASSISTANT) -> tuple[LiveResponseRegion, io.StringIO]:
    capture = io.StringIO()
    console = Console(
        file=capture,
        force_terminal=True,
        color_system="truecolor",
        width=80,
    )

    # Coordinator is not used by LiveResponseRegion's public API yet —
    # a minimal stub is enough for unit tests.
    class FakeCoord:
        pass

    region = LiveResponseRegion(
        console=console, coordinator=FakeCoord(), role=role,  # type: ignore[arg-type]
    )
    return region, capture


# === Lifecycle ===


def test_initial_state_is_active_and_empty():
    r, _ = _make()
    assert r.is_active is True
    assert r.buffer == ""
    assert r._committed is False
    assert r._aborted is False
    assert r._started is False


def test_start_creates_live_and_sets_started():
    r, _ = _make()
    r.start()
    assert r._started is True
    assert r._live is not None
    r.abort()


def test_start_is_idempotent():
    r, _ = _make()
    r.start()
    first_live = r._live
    r.start()
    assert r._live is first_live
    r.abort()


def test_feed_before_start_auto_starts():
    r, _ = _make()
    r.feed("hi")
    assert r._started is True
    assert r.buffer == "hi"
    r.abort()


def test_feed_accumulates_buffer():
    r, _ = _make()
    r.start()
    r.feed("hello ")
    r.feed("world")
    assert r.buffer == "hello world"
    r.abort()


# === Commit ===


def test_commit_stops_live_and_prints_scrollback():
    r, capture = _make()
    r.start()
    r.feed("# Title\n\nBody text.")
    r.commit()
    assert r._committed is True
    assert r.is_active is False
    out = capture.getvalue()
    # The final Markdown commit writes to scrollback; assert content
    # appears somewhere in the captured output.
    assert "Title" in out or "Body" in out


def test_commit_empty_buffer_does_not_crash():
    r, _ = _make()
    r.start()
    r.commit()
    # Empty commit is valid; just don't crash.
    assert r._committed is True
    assert r.is_active is False


def test_commit_clears_live_reference():
    r, _ = _make()
    r.start()
    r.feed("content")
    r.commit()
    assert r._live is None


def test_commit_after_commit_is_noop():
    r, _ = _make()
    r.start()
    r.feed("content")
    r.commit()
    r.commit()  # should not raise
    assert r._committed is True


def test_commit_after_abort_is_noop():
    r, _ = _make()
    r.start()
    r.feed("content")
    r.abort()
    r.commit()
    # Abort wins; commit is ignored.
    assert r._aborted is True
    assert r._committed is False


# === Abort ===


def test_abort_stops_live_and_preserves_buffer():
    r, _ = _make()
    r.start()
    r.feed("draft content")
    r.abort()
    assert r._aborted is True
    assert r.is_active is False
    assert r.buffer == "draft content"


def test_abort_does_not_print_duplicate_commit():
    """Regression guard: abort must NOT call the scrollback commit path."""
    r, capture = _make()
    r.start()
    r.feed("only-draft-marker")
    r.abort()
    out = capture.getvalue()
    # The Live region may flicker the content once before teardown,
    # but must never print the final commit render on top. So the
    # literal marker should appear at most once.
    assert out.count("only-draft-marker") <= 1


def test_abort_is_idempotent():
    r, _ = _make()
    r.start()
    r.feed("content")
    r.abort()
    r.abort()  # should not raise
    assert r._aborted is True


# === Feed after terminal state ===


def test_feed_after_commit_is_noop():
    r, _ = _make()
    r.start()
    r.feed("first")
    r.commit()
    r.feed("ignored")
    assert r.buffer == "first"


def test_feed_after_abort_is_noop():
    r, _ = _make()
    r.start()
    r.feed("first")
    r.abort()
    r.feed("ignored")
    assert r.buffer == "first"


# === Renderables ===


def test_render_in_progress_empty_returns_panel():
    r, _ = _make()
    renderable = r._render_in_progress()
    assert isinstance(renderable, Panel)


def test_render_in_progress_with_content_returns_panel():
    r, _ = _make()
    r._buffer = "# heading"
    renderable = r._render_in_progress()
    assert isinstance(renderable, Panel)


def test_render_final_is_plain_markdown():
    r, _ = _make()
    r._buffer = "plain **bold** text"
    renderable = r._render_final()
    assert isinstance(renderable, Markdown)


def test_role_in_in_progress_title():
    for role in (Role.ASSISTANT, Role.TOOL, Role.SYSTEM):
        r, _ = _make(role=role)
        r._buffer = "x"
        panel = r._render_in_progress()
        title_str = str(panel.title) if panel.title else ""
        assert role.value in title_str


def test_cursor_glyph_appears_in_empty_panel():
    """Empty buffer renders a cursor-only panel (the glyph is present)."""
    r, _ = _make()
    panel = r._render_in_progress()
    # The panel contains a Text renderable with the cursor glyph
    # inside. Rather than digging into Rich internals, render it to
    # a small console and assert the glyph appears.
    buf = io.StringIO()
    c = Console(file=buf, force_terminal=True, color_system="truecolor", width=40)
    c.print(panel)
    assert CURSOR_GLYPH in buf.getvalue()


# === Content variety ===


def test_unicode_content_roundtrip():
    r, capture = _make()
    r.start()
    r.feed("你好 🌏 world")
    r.commit()
    out = capture.getvalue()
    assert "你好" in out
    assert "world" in out


def test_long_buffer_commits_cleanly():
    r, capture = _make()
    r.start()
    # ~2KB of text
    for _ in range(100):
        r.feed("lorem ipsum dolor sit amet, ")
    r.commit()
    out = capture.getvalue()
    assert "lorem" in out
    assert r.is_active is False


def test_code_block_streaming():
    r, capture = _make()
    r.start()
    r.feed("Here is code:\n\n```python\n")
    r.feed("def hello():\n    pass\n")
    r.feed("```\n")
    r.commit()
    out = capture.getvalue()
    assert "hello" in out


# === Integration with REPLBackend ===


def test_backend_start_streaming_returns_live_region():
    """REPLBackend.start_streaming_message returns a LiveResponseRegion."""
    import io as _io

    from llm_code.view.repl.backend import REPLBackend

    console = Console(
        file=_io.StringIO(),
        force_terminal=True,
        color_system="truecolor",
        width=80,
    )
    backend = REPLBackend(console=console)
    handle = backend.start_streaming_message(role=Role.ASSISTANT)
    try:
        assert isinstance(handle, LiveResponseRegion)
        assert handle.is_active is True
    finally:
        handle.abort()


def test_backend_second_streaming_aborts_first():
    """Starting a new streaming region aborts the currently-active one."""
    import io as _io

    from llm_code.view.repl.backend import REPLBackend

    console = Console(
        file=_io.StringIO(),
        force_terminal=True,
        color_system="truecolor",
        width=80,
    )
    backend = REPLBackend(console=console)
    first = backend.start_streaming_message(role=Role.ASSISTANT)
    first.feed("unfinished")
    second = backend.start_streaming_message(role=Role.ASSISTANT)
    try:
        assert first.is_active is False  # aborted
        assert first._aborted is True
        assert second.is_active is True
    finally:
        second.abort()
