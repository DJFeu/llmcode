"""Tests for token_tracker — SQLite savings tracking and reporting."""
from __future__ import annotations

from pathlib import Path

from llm_code.tools.token_tracker import TokenTracker


class TestTokenTracker:
    def test_record_and_report(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        tracker = TokenTracker(db_path=db)

        tracker.record("git status", "git_status", 2000, 200, 90)
        tracker.record("pytest tests/", "pytest", 8000, 800, 90)
        tracker.record("git diff", "git_diff", 5000, 500, 90)

        report = tracker.report(days=30)
        assert report.total_commands == 3
        assert report.total_original == 15000
        assert report.total_compressed == 1500
        assert report.total_saved == 13500
        assert report.avg_pct == 90.0
        tracker.close()

    def test_empty_report(self, tmp_path: Path) -> None:
        db = tmp_path / "empty.db"
        tracker = TokenTracker(db_path=db)
        report = tracker.report(days=30)
        assert report.total_commands == 0
        assert report.total_saved == 0
        tracker.close()

    def test_format_report_empty(self, tmp_path: Path) -> None:
        db = tmp_path / "empty2.db"
        tracker = TokenTracker(db_path=db)
        text = tracker.format_report()
        assert "No compression data" in text
        tracker.close()

    def test_format_report_with_data(self, tmp_path: Path) -> None:
        db = tmp_path / "data.db"
        tracker = TokenTracker(db_path=db)
        for i in range(5):
            tracker.record(f"git status", "git_status", 2000, 200, 90)
        tracker.record("pytest tests/", "pytest", 10000, 1000, 90)

        text = tracker.format_report()
        assert "Token Savings Report" in text
        assert "Commands compressed:" in text
        assert "git_status" in text
        assert "pytest" in text
        tracker.close()

    def test_top_filters_ordering(self, tmp_path: Path) -> None:
        db = tmp_path / "order.db"
        tracker = TokenTracker(db_path=db)
        tracker.record("git status", "git_status", 1000, 100, 90)
        tracker.record("pytest", "pytest", 10000, 1000, 90)

        report = tracker.report()
        # pytest saves more total, should be first
        assert report.top_filters[0][0] == "pytest"
        tracker.close()

    def test_daily_breakdown(self, tmp_path: Path) -> None:
        db = tmp_path / "daily.db"
        tracker = TokenTracker(db_path=db)
        tracker.record("git status", "git_status", 2000, 200, 90)

        report = tracker.report()
        assert len(report.daily) >= 1
        tracker.close()

    def test_db_created_on_demand(self, tmp_path: Path) -> None:
        db = tmp_path / "subdir" / "tracker.db"
        tracker = TokenTracker(db_path=db)
        tracker.record("ls", "unknown", 100, 80, 20)
        assert db.exists()
        tracker.close()

    def test_close_idempotent(self, tmp_path: Path) -> None:
        db = tmp_path / "close.db"
        tracker = TokenTracker(db_path=db)
        tracker.record("x", "y", 100, 50, 50)
        tracker.close()
        tracker.close()  # Should not raise

    def test_format_report_shows_tokens(self, tmp_path: Path) -> None:
        db = tmp_path / "tokens.db"
        tracker = TokenTracker(db_path=db)
        tracker.record("git diff", "git_diff", 4000, 400, 90)
        text = tracker.format_report()
        assert "tokens" in text
        tracker.close()

    def test_report_days_filter(self, tmp_path: Path) -> None:
        """Report with days=0 should still work (returns all recent)."""
        db = tmp_path / "days.db"
        tracker = TokenTracker(db_path=db)
        tracker.record("x", "y", 100, 50, 50)
        report = tracker.report(days=0)
        # days=0 means cutoff is right now, so nothing matches
        assert report.total_commands == 0
        tracker.close()
