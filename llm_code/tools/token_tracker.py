"""Track token savings from output compression in a SQLite database.

Storage: ``~/.llmcode/token-savings.db``
Schema: single ``compressions`` table with auto-cleanup at 90 days.
"""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

_log = logging.getLogger(__name__)

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS compressions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    command TEXT NOT NULL,
    filter_type TEXT NOT NULL,
    original_chars INTEGER NOT NULL,
    compressed_chars INTEGER NOT NULL,
    saved_pct INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_compressions_ts ON compressions(timestamp);
"""

_CLEANUP_DAYS = 90


@dataclass(frozen=True)
class SavingsReport:
    total_commands: int
    total_original: int
    total_compressed: int
    total_saved: int
    avg_pct: float
    top_filters: list[tuple[str, int, int]]   # (filter_type, count, total_saved)
    top_commands: list[tuple[str, int, int]]   # (command_prefix, count, total_saved)
    daily: list[tuple[str, int, int]]          # (date, count, saved)


class TokenTracker:
    """Records and queries compression savings."""

    def __init__(self, db_path: Path | None = None) -> None:
        if db_path is None:
            db_path = Path.home() / ".llmcode" / "token-savings.db"
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None

    def _connect(self) -> sqlite3.Connection:
        if self._conn is not None:
            return self._conn
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path))
        self._conn.executescript(_SCHEMA)
        return self._conn

    def record(
        self,
        command: str,
        filter_type: str,
        original_chars: int,
        compressed_chars: int,
        saved_pct: int,
    ) -> None:
        """Record a compression event.  Silently ignores errors."""
        try:
            conn = self._connect()
            now = datetime.now(tz=timezone.utc).isoformat()
            conn.execute(
                "INSERT INTO compressions (timestamp, command, filter_type, original_chars, compressed_chars, saved_pct)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (now, command[:200], filter_type, original_chars, compressed_chars, saved_pct),
            )
            conn.commit()
            self._cleanup(conn)
        except Exception:
            _log.debug("Failed to record token savings", exc_info=True)

    @staticmethod
    def _cleanup(conn: sqlite3.Connection) -> None:
        cutoff = (datetime.now(tz=timezone.utc) - timedelta(days=_CLEANUP_DAYS)).isoformat()
        try:
            conn.execute("DELETE FROM compressions WHERE timestamp < ?", (cutoff,))
            conn.commit()
        except Exception:
            pass

    def report(self, days: int = 30) -> SavingsReport:
        """Generate a savings report for the last N days."""
        conn = self._connect()
        cutoff = (datetime.now(tz=timezone.utc) - timedelta(days=days)).isoformat()

        # Totals
        row = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(original_chars),0), COALESCE(SUM(compressed_chars),0),"
            " COALESCE(AVG(saved_pct),0)"
            " FROM compressions WHERE timestamp >= ?",
            (cutoff,),
        ).fetchone()
        total_commands = row[0]
        total_original = row[1]
        total_compressed = row[2]
        avg_pct = row[3]
        total_saved = total_original - total_compressed

        # Top filters
        top_filters = conn.execute(
            "SELECT filter_type, COUNT(*), SUM(original_chars - compressed_chars)"
            " FROM compressions WHERE timestamp >= ?"
            " GROUP BY filter_type ORDER BY 3 DESC LIMIT 10",
            (cutoff,),
        ).fetchall()

        # Top commands (first word only)
        top_commands_raw = conn.execute(
            "SELECT command, COUNT(*), SUM(original_chars - compressed_chars)"
            " FROM compressions WHERE timestamp >= ?"
            " GROUP BY command ORDER BY 3 DESC LIMIT 10",
            (cutoff,),
        ).fetchall()
        # Shorten command to first 40 chars
        top_commands = [(c[:40], n, s) for c, n, s in top_commands_raw]

        # Daily breakdown
        daily = conn.execute(
            "SELECT DATE(timestamp), COUNT(*), SUM(original_chars - compressed_chars)"
            " FROM compressions WHERE timestamp >= ?"
            " GROUP BY DATE(timestamp) ORDER BY 1 DESC LIMIT 30",
            (cutoff,),
        ).fetchall()

        return SavingsReport(
            total_commands=total_commands,
            total_original=total_original,
            total_compressed=total_compressed,
            total_saved=total_saved,
            avg_pct=round(avg_pct, 1),
            top_filters=top_filters,
            top_commands=top_commands,
            daily=daily,
        )

    def format_report(self, days: int = 30) -> str:
        """Format a human-readable savings report."""
        r = self.report(days)

        if r.total_commands == 0:
            return "No compression data yet. Run some commands to start tracking savings."

        lines: list[str] = []
        lines.append(f"Token Savings Report (last {days} days)")
        lines.append("=" * 45)
        lines.append(f"Commands compressed:  {r.total_commands:,}")
        lines.append(f"Original output:      {r.total_original:,} chars (~{r.total_original // 4:,} tokens)")
        lines.append(f"Compressed output:    {r.total_compressed:,} chars (~{r.total_compressed // 4:,} tokens)")
        lines.append(f"Total saved:          {r.total_saved:,} chars (~{r.total_saved // 4:,} tokens)")
        lines.append(f"Average savings:      {r.avg_pct}%")

        if r.top_filters:
            lines.append("")
            lines.append("Top filters:")
            for ftype, count, saved in r.top_filters:
                lines.append(f"  {ftype:<20} {count:>5}x  {saved:>8,} chars saved")

        if r.top_commands:
            lines.append("")
            lines.append("Top commands:")
            for cmd, count, saved in r.top_commands:
                lines.append(f"  {cmd:<40} {count:>3}x  {saved:>8,} saved")

        if r.daily and len(r.daily) > 1:
            lines.append("")
            lines.append("Daily breakdown:")
            for date, count, saved in r.daily[:7]:
                bar = "█" * min(saved // 500, 30) if saved > 0 else ""
                lines.append(f"  {date}  {count:>4} cmds  {saved:>8,} saved  {bar}")

        return "\n".join(lines)

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
