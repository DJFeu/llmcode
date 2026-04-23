"""Smoke tests for v12 M7 Task 7.9 — ``llmcode memory migrate`` CLI.

Uses :class:`click.testing.CliRunner`; the CLI group is not wired into
the main ``llmcode`` entry point yet (that lands in a later milestone).
"""
from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner


def _write_v10_index(path: Path, entries: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps({"magic": "HIDA_IDX", "schema_version": 2}) + "\n")
        for e in entries:
            fh.write(json.dumps(e) + "\n")


class TestMemoryMigrateCLI:
    def test_memory_group_exists(self) -> None:
        from llm_code.memory.cli import memory

        assert memory.name == "memory"
        assert "migrate" in memory.commands

    def test_dry_run_reports_counts(self, tmp_path: Path) -> None:
        from llm_code.memory.cli import memory

        src = tmp_path / "v10.idx"
        dst = tmp_path / "v12.idx"
        _write_v10_index(
            src,
            [
                {
                    "id": f"e{i}",
                    "text": "hello",
                    "embedding": [0.1, 0.2],
                    "source": "bash",
                    "created_at": "2025-06-15T10:30:00",
                    "scope": "project",
                }
                for i in range(3)
            ],
        )

        runner = CliRunner()
        result = runner.invoke(
            memory,
            [
                "migrate",
                "--from",
                str(src),
                "--to",
                str(dst),
                "--dry-run",
            ],
        )

        assert result.exit_code == 0, result.output
        assert not dst.exists()
        assert "3" in result.output  # entries read / written

    def test_missing_source_file_errors(self, tmp_path: Path) -> None:
        from llm_code.memory.cli import memory

        runner = CliRunner()
        result = runner.invoke(
            memory,
            [
                "migrate",
                "--from",
                str(tmp_path / "missing.idx"),
                "--to",
                str(tmp_path / "v12.idx"),
            ],
        )

        # click surfaces FileNotFoundError as a non-zero exit.
        assert result.exit_code != 0

    def test_normal_run_writes_target_file(self, tmp_path: Path) -> None:
        from llm_code.memory.cli import memory

        src = tmp_path / "v10.idx"
        dst = tmp_path / "v12.idx"
        _write_v10_index(
            src,
            [
                {
                    "id": "e1",
                    "text": "note",
                    "embedding": [0.1],
                    "source": "read",
                    "created_at": "2025-06-15T10:30:00",
                    "scope": "project",
                },
            ],
        )

        runner = CliRunner()
        result = runner.invoke(
            memory,
            [
                "migrate",
                "--from",
                str(src),
                "--to",
                str(dst),
            ],
        )

        assert result.exit_code == 0, result.output
        assert dst.exists()
