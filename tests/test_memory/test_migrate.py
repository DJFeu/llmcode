"""Tests for v12 M7 Task 7.9 — v10 → v12 memory index migration.

The migration script does not depend on a real HIDA index file; the
storage format is an internal JSON-lines convention where the first line
is a magic+version header and subsequent lines are entry records.

- ``schema_version`` ≤ 2 → legacy (v10 / v11).
- ``schema_version`` == 3 → v12 (target).
- Non-matching / missing header → 0 (undetermined).

Plan: docs/superpowers/plans/2026-04-21-llm-code-v12-memory-components.md
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest


# Deterministic frozen timestamps used in fixtures to avoid flakiness.
LEGACY_NAIVE_ISO = "2025-06-15T10:30:00"
LEGACY_UTC_ISO = "2025-06-15T10:30:00+00:00"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_v10_index(path: Path, entries: list[dict]) -> None:
    """Write a synthetic v10 HIDA-shaped index (schema_version=2)."""
    with path.open("w", encoding="utf-8") as fh:
        header = {"magic": "HIDA_IDX", "schema_version": 2}
        fh.write(json.dumps(header) + "\n")
        for e in entries:
            fh.write(json.dumps(e) + "\n")


def _write_v11_index(path: Path, entries: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        header = {"magic": "HIDA_IDX", "schema_version": 1}
        fh.write(json.dumps(header) + "\n")
        for e in entries:
            fh.write(json.dumps(e) + "\n")


def _write_v12_index(path: Path, entries: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        header = {"magic": "HIDA_IDX", "schema_version": 3}
        fh.write(json.dumps(header) + "\n")
        for e in entries:
            fh.write(json.dumps(e) + "\n")


def _legacy_entry(
    *,
    entry_id: str = "e1",
    text: str = "hello",
    embedding: list[float] | None = None,
    source: str | None = "bash:ls -la",
    created_at: str = LEGACY_NAIVE_ISO,
    scope: str = "project",
    extra: dict | None = None,
) -> dict:
    e: dict = {
        "id": entry_id,
        "text": text,
        "embedding": embedding if embedding is not None else [0.1, 0.2, 0.3],
        "created_at": created_at,
        "scope": scope,
    }
    if source is not None:
        e["source"] = source
    if extra:
        e.update(extra)
    return e


# ---------------------------------------------------------------------------
# MigrationReport
# ---------------------------------------------------------------------------


class TestMigrationReport:
    def test_has_required_fields(self) -> None:
        import dataclasses

        from llm_code.memory.migrate import MigrationReport

        assert dataclasses.is_dataclass(MigrationReport)
        field_names = {f.name for f in dataclasses.fields(MigrationReport)}
        assert field_names == {
            "entries_read",
            "entries_written",
            "duration_s",
            "warnings",
            "schema_from",
            "schema_to",
        }


# ---------------------------------------------------------------------------
# detect_schema_version
# ---------------------------------------------------------------------------


class TestDetectSchemaVersion:
    def test_v10_schema(self, tmp_path: Path) -> None:
        from llm_code.memory.migrate import detect_schema_version

        idx = tmp_path / "hida.idx"
        _write_v10_index(idx, [])
        assert detect_schema_version(idx) == 2

    def test_v11_schema(self, tmp_path: Path) -> None:
        from llm_code.memory.migrate import detect_schema_version

        idx = tmp_path / "hida.idx"
        _write_v11_index(idx, [])
        assert detect_schema_version(idx) == 1

    def test_v12_schema(self, tmp_path: Path) -> None:
        from llm_code.memory.migrate import detect_schema_version

        idx = tmp_path / "hida.idx"
        _write_v12_index(idx, [])
        assert detect_schema_version(idx) == 3

    def test_missing_file_returns_zero(self, tmp_path: Path) -> None:
        from llm_code.memory.migrate import detect_schema_version

        assert detect_schema_version(tmp_path / "missing.idx") == 0

    def test_malformed_header_returns_zero(self, tmp_path: Path) -> None:
        from llm_code.memory.migrate import detect_schema_version

        idx = tmp_path / "bad.idx"
        idx.write_text("garbage-no-header\n", encoding="utf-8")
        assert detect_schema_version(idx) == 0

    def test_empty_file_returns_zero(self, tmp_path: Path) -> None:
        from llm_code.memory.migrate import detect_schema_version

        idx = tmp_path / "empty.idx"
        idx.write_text("", encoding="utf-8")
        assert detect_schema_version(idx) == 0

    def test_wrong_magic_returns_zero(self, tmp_path: Path) -> None:
        from llm_code.memory.migrate import detect_schema_version

        idx = tmp_path / "wrong.idx"
        idx.write_text(
            json.dumps({"magic": "OTHER", "schema_version": 2}) + "\n",
            encoding="utf-8",
        )
        assert detect_schema_version(idx) == 0


# ---------------------------------------------------------------------------
# migrate_index — counts + conversions
# ---------------------------------------------------------------------------


class TestMigrateIndexCounts:
    def test_entry_count_preserved(self, tmp_path: Path) -> None:
        from llm_code.memory.migrate import migrate_index

        src = tmp_path / "v10.idx"
        dst = tmp_path / "v12.idx"
        _write_v10_index(
            src,
            [
                _legacy_entry(entry_id=f"e{i}")
                for i in range(5)
            ],
        )

        report = migrate_index(src, dst)
        assert report.entries_read == 5
        assert report.entries_written == 5

    def test_schema_from_to_populated(self, tmp_path: Path) -> None:
        from llm_code.memory.migrate import migrate_index

        src = tmp_path / "v10.idx"
        dst = tmp_path / "v12.idx"
        _write_v10_index(src, [_legacy_entry()])

        report = migrate_index(src, dst)
        assert report.schema_from == 2
        assert report.schema_to == 3

    def test_duration_recorded(self, tmp_path: Path) -> None:
        from llm_code.memory.migrate import migrate_index

        src = tmp_path / "v10.idx"
        dst = tmp_path / "v12.idx"
        _write_v10_index(src, [_legacy_entry()])

        report = migrate_index(src, dst)
        assert report.duration_s >= 0.0

    def test_output_file_has_v12_header(self, tmp_path: Path) -> None:
        from llm_code.memory.migrate import detect_schema_version, migrate_index

        src = tmp_path / "v10.idx"
        dst = tmp_path / "v12.idx"
        _write_v10_index(src, [_legacy_entry()])

        migrate_index(src, dst)
        assert detect_schema_version(dst) == 3


class TestMigrateIndexConversion:
    def test_naive_datetime_converted_to_utc(self, tmp_path: Path) -> None:
        from llm_code.memory.migrate import migrate_index

        src = tmp_path / "v10.idx"
        dst = tmp_path / "v12.idx"
        _write_v10_index(src, [_legacy_entry(created_at=LEGACY_NAIVE_ISO)])

        migrate_index(src, dst)

        # Read back the converted entry and inspect its created_at.
        lines = dst.read_text(encoding="utf-8").splitlines()
        # first line is header, second is the entry
        entry = json.loads(lines[1])
        created = datetime.fromisoformat(entry["created_at"])
        assert created.tzinfo is not None
        assert created.utcoffset() == timezone.utc.utcoffset(created)

    def test_aware_datetime_preserved(self, tmp_path: Path) -> None:
        from llm_code.memory.migrate import migrate_index

        src = tmp_path / "v10.idx"
        dst = tmp_path / "v12.idx"
        _write_v10_index(
            src,
            [_legacy_entry(created_at="2025-06-15T10:30:00+00:00")],
        )

        migrate_index(src, dst)

        lines = dst.read_text(encoding="utf-8").splitlines()
        entry = json.loads(lines[1])
        assert datetime.fromisoformat(entry["created_at"]).tzinfo is not None

    def test_legacy_source_split_into_source_tool(self, tmp_path: Path) -> None:
        from llm_code.memory.migrate import migrate_index

        src = tmp_path / "v10.idx"
        dst = tmp_path / "v12.idx"
        _write_v10_index(src, [_legacy_entry(source="bash:ls -la")])

        migrate_index(src, dst)

        lines = dst.read_text(encoding="utf-8").splitlines()
        entry = json.loads(lines[1])
        assert entry["source_tool"] == "bash"

    def test_legacy_source_without_colon_used_as_tool(
        self, tmp_path: Path,
    ) -> None:
        from llm_code.memory.migrate import migrate_index

        src = tmp_path / "v10.idx"
        dst = tmp_path / "v12.idx"
        _write_v10_index(src, [_legacy_entry(source="read")])

        migrate_index(src, dst)

        lines = dst.read_text(encoding="utf-8").splitlines()
        entry = json.loads(lines[1])
        assert entry["source_tool"] == "read"

    def test_missing_source_yields_none_source_tool(self, tmp_path: Path) -> None:
        from llm_code.memory.migrate import migrate_index

        src = tmp_path / "v10.idx"
        dst = tmp_path / "v12.idx"
        _write_v10_index(src, [_legacy_entry(source=None)])

        migrate_index(src, dst)

        lines = dst.read_text(encoding="utf-8").splitlines()
        entry = json.loads(lines[1])
        assert entry["source_tool"] is None

    def test_metadata_migrated_from_v10_flag(self, tmp_path: Path) -> None:
        from llm_code.memory.migrate import migrate_index

        src = tmp_path / "v10.idx"
        dst = tmp_path / "v12.idx"
        _write_v10_index(src, [_legacy_entry()])

        migrate_index(src, dst)

        lines = dst.read_text(encoding="utf-8").splitlines()
        entry = json.loads(lines[1])
        assert entry["metadata"]["migrated_from_v10"] is True

    def test_embedding_preserved_verbatim(self, tmp_path: Path) -> None:
        from llm_code.memory.migrate import migrate_index

        src = tmp_path / "v10.idx"
        dst = tmp_path / "v12.idx"
        original = [0.1, -0.5, 0.9, 42.0]
        _write_v10_index(src, [_legacy_entry(embedding=original)])

        migrate_index(src, dst)

        lines = dst.read_text(encoding="utf-8").splitlines()
        entry = json.loads(lines[1])
        assert entry["embedding"] == original

    def test_convert_entry_returns_memory_entry(self, tmp_path: Path) -> None:
        """The private helper returns a MemoryEntry instance for reuse."""
        from llm_code.engine.components.memory.schema import (
            MemoryEntry,
            MemoryScope,
        )
        from llm_code.memory.migrate import _convert_entry

        legacy = _legacy_entry(
            entry_id="e1",
            text="note",
            embedding=[1.0, 2.0],
            source="read",
            scope="project",
        )
        entry = _convert_entry(legacy)

        assert isinstance(entry, MemoryEntry)
        assert entry.id == "e1"
        assert entry.text == "note"
        assert entry.embedding == (1.0, 2.0)
        assert entry.scope is MemoryScope.PROJECT
        assert entry.source_tool == "read"
        assert entry.metadata["migrated_from_v10"] is True
        assert entry.created_at.tzinfo is not None


# ---------------------------------------------------------------------------
# Dry-run + warnings
# ---------------------------------------------------------------------------


class TestMigrateIndexDryRun:
    def test_dry_run_does_not_write(self, tmp_path: Path) -> None:
        from llm_code.memory.migrate import migrate_index

        src = tmp_path / "v10.idx"
        dst = tmp_path / "v12.idx"
        _write_v10_index(src, [_legacy_entry() for _ in range(3)])

        report = migrate_index(src, dst, dry_run=True)

        assert report.entries_read == 3
        assert report.entries_written == 3  # counted as would-write
        assert not dst.exists()

    def test_dry_run_preserves_source_file(self, tmp_path: Path) -> None:
        from llm_code.memory.migrate import migrate_index

        src = tmp_path / "v10.idx"
        dst = tmp_path / "v12.idx"
        _write_v10_index(src, [_legacy_entry()])
        original_bytes = src.read_bytes()

        migrate_index(src, dst, dry_run=True)

        assert src.read_bytes() == original_bytes


class TestMigrateIndexWarnings:
    def test_missing_source_file_raises_file_not_found(self, tmp_path: Path) -> None:
        from llm_code.memory.migrate import migrate_index

        src = tmp_path / "missing.idx"
        dst = tmp_path / "v12.idx"

        with pytest.raises(FileNotFoundError):
            migrate_index(src, dst)

    def test_non_round_trippable_extra_field_emits_warning(
        self, tmp_path: Path,
    ) -> None:
        from llm_code.memory.migrate import migrate_index

        src = tmp_path / "v10.idx"
        dst = tmp_path / "v12.idx"
        _write_v10_index(
            src,
            [
                _legacy_entry(
                    extra={"obscure_legacy_field": "something"},
                ),
            ],
        )

        report = migrate_index(src, dst)

        assert any(
            "obscure_legacy_field" in w for w in report.warnings
        ), report.warnings

    def test_already_v12_schema_no_migration(self, tmp_path: Path) -> None:
        from llm_code.memory.migrate import migrate_index

        src = tmp_path / "already.idx"
        dst = tmp_path / "out.idx"
        _write_v12_index(src, [])

        report = migrate_index(src, dst)

        assert report.schema_from == 3
        assert report.schema_to == 3
        # Must not silently drop; warns the caller.
        assert any("already" in w.lower() for w in report.warnings)

    def test_undetectable_schema_raises_value_error(self, tmp_path: Path) -> None:
        from llm_code.memory.migrate import migrate_index

        src = tmp_path / "bad.idx"
        dst = tmp_path / "out.idx"
        src.write_text("not-an-index\n", encoding="utf-8")

        with pytest.raises(ValueError):
            migrate_index(src, dst)
