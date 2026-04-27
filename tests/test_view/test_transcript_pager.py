"""Tests for the v16 M10 transcript pager.

The pager is model-first (no prompt_toolkit dependency), so these
tests exercise the navigation, search, and rendering surface
directly.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from llm_code.runtime.state_db import StateDB
from llm_code.view.repl.components.transcript_pager import (
    PagerLine,
    SearchState,
    TranscriptPager,
    render_lines,
)


@pytest.fixture
def db_with_session(tmp_path: Path) -> StateDB:
    db = StateDB(tmp_path / "state.db")
    db.upsert_session("s1", {"id": "s1"})
    for i in range(20):
        db.append_turn(
            f"t{i}", "s1", i,
            f"u{i}: question {i}",
            f"a{i}: answer to question {i}",
        )
    return db


def test_open_loads_lines_and_positions_cursor(db_with_session: StateDB) -> None:
    pager = TranscriptPager(
        state_db=db_with_session, session_id="s1",
        max_turns=5, viewport_height=4,
    )
    pager.open()
    assert pager.is_open
    assert pager.line_count > 0
    # Cursor positioned near the bottom so the latest content is visible
    assert pager.cursor + pager.viewport_height >= pager.line_count


def test_close_clears_state(db_with_session: StateDB) -> None:
    pager = TranscriptPager(
        state_db=db_with_session, session_id="s1", max_turns=5,
    )
    pager.open()
    pager.close()
    assert not pager.is_open
    assert pager.line_count == 0


def test_navigation_up_down(db_with_session: StateDB) -> None:
    pager = TranscriptPager(
        state_db=db_with_session, session_id="s1", max_turns=10,
        viewport_height=3,
    )
    pager.open()
    pager.goto_start()
    assert pager.cursor == 0
    pager.scroll_down(2)
    assert pager.cursor == 2
    pager.scroll_up(1)
    assert pager.cursor == 1
    pager.goto_end()
    assert pager.cursor == max(0, pager.line_count - pager.viewport_height)


def test_page_up_down(db_with_session: StateDB) -> None:
    pager = TranscriptPager(
        state_db=db_with_session, session_id="s1", max_turns=10,
        viewport_height=4,
    )
    pager.open()
    pager.goto_start()
    pager.page_down()
    assert pager.cursor == 4
    pager.page_up()
    assert pager.cursor == 0


def test_search_finds_match_and_centres(db_with_session: StateDB) -> None:
    pager = TranscriptPager(
        state_db=db_with_session, session_id="s1", max_turns=10,
        viewport_height=4,
    )
    pager.open()
    pager.begin_search()
    for ch in "answer":
        pager.update_search_buffer(ch)
    state = pager.commit_search()
    assert isinstance(state, SearchState)
    assert state.matches  # at least one match
    assert state.cursor == 0
    assert state.needle == "answer"


def test_search_next_prev_cycle(db_with_session: StateDB) -> None:
    pager = TranscriptPager(
        state_db=db_with_session, session_id="s1", max_turns=10,
        viewport_height=4,
    )
    pager.open()
    pager.begin_search()
    for ch in "question":
        pager.update_search_buffer(ch)
    state = pager.commit_search()
    initial_cursor = state.cursor
    state2 = pager.next_match()
    if len(state.matches) > 1:
        assert state2.cursor != initial_cursor
    state3 = pager.prev_match()
    assert state3.cursor == initial_cursor


def test_search_no_match_keeps_state_with_empty_matches(
    db_with_session: StateDB,
) -> None:
    pager = TranscriptPager(
        state_db=db_with_session, session_id="s1", max_turns=10,
    )
    pager.open()
    pager.begin_search()
    for ch in "zebrafish":
        pager.update_search_buffer(ch)
    state = pager.commit_search()
    assert state.matches == ()
    assert state.cursor == -1
    assert "no matches" in pager.status_line()


def test_search_backspace_and_cancel(db_with_session: StateDB) -> None:
    pager = TranscriptPager(
        state_db=db_with_session, session_id="s1", max_turns=10,
    )
    pager.open()
    pager.begin_search()
    pager.update_search_buffer("a")
    pager.update_search_buffer("b")
    pager.backspace_search()
    pager.commit_search()
    assert pager.search is not None
    assert pager.search.needle == "a"
    pager.cancel_search()
    assert pager.search is None


def test_current_view_returns_visible_slice(db_with_session: StateDB) -> None:
    pager = TranscriptPager(
        state_db=db_with_session, session_id="s1", max_turns=5,
        viewport_height=3,
    )
    pager.open()
    pager.goto_start()
    view = pager.current_view()
    assert len(view) == 3
    assert all(isinstance(line, PagerLine) for line in view)


def test_current_view_marks_search_matches(db_with_session: StateDB) -> None:
    pager = TranscriptPager(
        state_db=db_with_session, session_id="s1", max_turns=10,
        viewport_height=20,
    )
    pager.open()
    pager.goto_start()
    pager.begin_search()
    for ch in "answer":
        pager.update_search_buffer(ch)
    pager.commit_search()
    view = pager.current_view()
    assert any(line.is_match for line in view)


def test_status_line_reports_position(db_with_session: StateDB) -> None:
    pager = TranscriptPager(
        state_db=db_with_session, session_id="s1", max_turns=3,
        viewport_height=2,
    )
    pager.open()
    pager.goto_start()
    status = pager.status_line()
    assert "lines 1-2/" in status


def test_render_lines_handles_empty_input() -> None:
    assert render_lines([]) == []


def test_status_line_reports_match_position(db_with_session: StateDB) -> None:
    pager = TranscriptPager(
        state_db=db_with_session, session_id="s1", max_turns=5,
        viewport_height=20,
    )
    pager.open()
    pager.begin_search()
    for ch in "answer":
        pager.update_search_buffer(ch)
    pager.commit_search()
    status = pager.status_line()
    assert "match" in status
