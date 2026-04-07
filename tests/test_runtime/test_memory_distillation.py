"""Tests for distill_daily: today → recent → archive rollover."""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from llm_code.runtime.memory_layers import distill_daily


def test_today_drains_into_recent(tmp_path: Path) -> None:
    yesterday = date(2026, 4, 6)
    today = date(2026, 4, 7)
    (tmp_path / f"today-{yesterday.isoformat()}.md").write_text("yesterday note")

    distill_daily(tmp_path, today)

    recent = (tmp_path / "recent.md").read_text()
    assert "yesterday note" in recent
    assert f"<!-- entry: {yesterday.isoformat()} -->" in recent
    assert not (tmp_path / f"today-{yesterday.isoformat()}.md").exists()


def test_today_for_today_is_not_drained(tmp_path: Path) -> None:
    today = date(2026, 4, 7)
    today_file = tmp_path / f"today-{today.isoformat()}.md"
    today_file.write_text("happening now")

    distill_daily(tmp_path, today)

    assert today_file.exists()
    assert not (tmp_path / "recent.md").exists()


def test_recent_older_than_7_days_moves_to_archive(tmp_path: Path) -> None:
    today = date(2026, 4, 14)
    old = date(2026, 4, 1)  # 13 days old
    (tmp_path / "recent.md").write_text(
        f"<!-- entry: {old.isoformat()} -->\nold note\n"
    )

    distill_daily(tmp_path, today)

    archive = (tmp_path / "archive.md").read_text()
    assert "old note" in archive
    recent = (tmp_path / "recent.md").read_text()
    assert "old note" not in recent


def test_idempotent(tmp_path: Path) -> None:
    yesterday = date(2026, 4, 6)
    today = date(2026, 4, 7)
    (tmp_path / f"today-{yesterday.isoformat()}.md").write_text("note")

    distill_daily(tmp_path, today)
    snap1 = (tmp_path / "recent.md").read_text()

    distill_daily(tmp_path, today)
    snap2 = (tmp_path / "recent.md").read_text()
    assert snap1 == snap2

    # And again with the archive cycle
    later = today + timedelta(days=10)
    distill_daily(tmp_path, later)
    snap3a = (tmp_path / "archive.md").read_text()
    distill_daily(tmp_path, later)
    snap3b = (tmp_path / "archive.md").read_text()
    assert snap3a == snap3b
