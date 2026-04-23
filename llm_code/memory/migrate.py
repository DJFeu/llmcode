"""v10 → v12 HIDA index migration (M7 Task 7.9).

The storage format is an internal JSON-Lines convention:

- Line 1 — header: ``{"magic": "HIDA_IDX", "schema_version": N}``.
  * ``N ≤ 2``: legacy (v10 / v11).
  * ``N == 3``: v12 (target).
- Lines 2…: one entry object per line (``id``, ``text``, ``embedding``,
  ``source``, ``created_at``, ``scope``, optional extra legacy fields).

The migration streams the source, converts each entry to the v12 shape
(naive datetimes → UTC, ``source`` → ``source_tool`` + metadata, add
``metadata.migrated_from_v10 = True``), and writes a new header + entry
stream to the destination. In ``dry_run=True`` mode no bytes are written
but counts + warnings are produced so callers can preview the migration.

Plan: docs/superpowers/plans/2026-04-21-llm-code-v12-memory-components.md
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from llm_code.engine.components.memory.schema import MemoryEntry, MemoryScope

__all__ = [
    "MIGRATION_MAGIC",
    "MigrationReport",
    "V12_SCHEMA_VERSION",
    "detect_schema_version",
    "migrate_index",
]

MIGRATION_MAGIC = "HIDA_IDX"
V12_SCHEMA_VERSION = 3

# Keys the migrator explicitly knows how to map. Anything else triggers a
# warning so operators know some legacy metadata did not round-trip.
_KNOWN_LEGACY_KEYS = frozenset(
    {"id", "text", "embedding", "source", "created_at", "scope"},
)


@dataclass
class MigrationReport:
    """Result of a migrate_index call — counts, timing, and warnings."""

    entries_read: int = 0
    entries_written: int = 0
    duration_s: float = 0.0
    warnings: tuple[str, ...] = ()
    schema_from: int = 0
    schema_to: int = V12_SCHEMA_VERSION


def detect_schema_version(path: Path) -> int:
    """Return the schema version declared in *path*'s header line.

    - ``v12`` target: ``3``.
    - ``v10 / v11`` legacy: ``1`` or ``2``.
    - Any other condition (missing file, empty, non-JSON, wrong magic):
      ``0``.
    """
    if not path.exists() or not path.is_file():
        return 0
    try:
        with path.open("r", encoding="utf-8") as fh:
            first_line = fh.readline()
    except OSError:
        return 0
    if not first_line.strip():
        return 0
    try:
        header = json.loads(first_line)
    except json.JSONDecodeError:
        return 0
    if not isinstance(header, dict):
        return 0
    if header.get("magic") != MIGRATION_MAGIC:
        return 0
    version = header.get("schema_version")
    if not isinstance(version, int):
        return 0
    return version


def migrate_index(
    src: Path,
    dst: Path,
    *,
    dry_run: bool = False,
) -> MigrationReport:
    """Stream-migrate a legacy HIDA index at *src* into v12 shape at *dst*.

    Raises:
        FileNotFoundError: *src* does not exist.
        ValueError: header is undetectable or malformed.
    """
    if not src.exists() or not src.is_file():
        raise FileNotFoundError(f"Source index not found: {src}")

    schema_from = detect_schema_version(src)
    if schema_from == 0:
        raise ValueError(
            f"Could not detect schema version for {src}; "
            f"missing or invalid HIDA header",
        )

    warnings: list[str] = []

    if schema_from == V12_SCHEMA_VERSION:
        warnings.append(
            f"{src} is already v12 (schema_version={V12_SCHEMA_VERSION}); "
            f"no migration needed",
        )
        return MigrationReport(
            entries_read=0,
            entries_written=0,
            duration_s=0.0,
            warnings=tuple(warnings),
            schema_from=schema_from,
            schema_to=V12_SCHEMA_VERSION,
        )

    start = time.monotonic()
    entries_read = 0
    entries_written = 0

    if dry_run:
        # Count-only pass — still exercise conversion so any warnings
        # surface identically to a real run.
        for legacy in _iter_legacy_entries(src):
            entries_read += 1
            _convert_entry(legacy, warnings=warnings)
            entries_written += 1
    else:
        dst.parent.mkdir(parents=True, exist_ok=True)
        with dst.open("w", encoding="utf-8") as out_fh:
            out_fh.write(
                json.dumps(
                    {
                        "magic": MIGRATION_MAGIC,
                        "schema_version": V12_SCHEMA_VERSION,
                    },
                )
                + "\n",
            )
            for legacy in _iter_legacy_entries(src):
                entries_read += 1
                entry = _convert_entry(legacy, warnings=warnings)
                out_fh.write(json.dumps(_entry_to_json(entry)) + "\n")
                entries_written += 1

    duration = time.monotonic() - start
    return MigrationReport(
        entries_read=entries_read,
        entries_written=entries_written,
        duration_s=duration,
        warnings=tuple(warnings),
        schema_from=schema_from,
        schema_to=V12_SCHEMA_VERSION,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _iter_legacy_entries(path: Path) -> Iterator[dict[str, Any]]:
    """Yield parsed entry dicts, skipping the header line.

    Silently skips blank / unparseable lines so a partially corrupt index
    does not abort the whole migration — each skipped line adds nothing
    to ``entries_read``. The header line is already accounted for via
    ``detect_schema_version``.
    """
    with path.open("r", encoding="utf-8") as fh:
        for i, raw in enumerate(fh):
            if i == 0:
                continue  # header line already consumed
            line = raw.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                yield obj


def _convert_entry(
    legacy: dict[str, Any],
    *,
    warnings: list[str] | None = None,
) -> MemoryEntry:
    """Map a legacy entry dict onto a v12 :class:`MemoryEntry`.

    - ``created_at``: parsed as ISO; naive datetimes are pinned to UTC.
    - ``source``: split on first ``:`` into ``source_tool`` (head);
      whole string preserved under ``metadata['source_raw']`` for audit.
    - ``metadata['migrated_from_v10']`` is always set to ``True``.
    - Any legacy keys outside the known set surface as warnings and are
      preserved under ``metadata['unknown_legacy']``.
    """
    entry_id = str(legacy.get("id", ""))
    text = str(legacy.get("text", ""))

    raw_embedding = legacy.get("embedding")
    embedding: tuple[float, ...] | None
    if raw_embedding is None:
        embedding = None
    else:
        embedding = tuple(float(x) for x in raw_embedding)

    scope = _coerce_scope(legacy.get("scope"))
    created_at = _coerce_datetime(legacy.get("created_at"))

    source_tool: str | None = None
    metadata: dict[str, Any] = {"migrated_from_v10": True}

    source_raw = legacy.get("source")
    if source_raw is not None:
        source_str = str(source_raw)
        metadata["source_raw"] = source_str
        source_tool = source_str.split(":", 1)[0] if source_str else None

    # Warn on any non-round-trippable extra fields. Preserve them in
    # metadata so no data is silently lost.
    unknown = {
        k: v for k, v in legacy.items() if k not in _KNOWN_LEGACY_KEYS
    }
    if unknown:
        if warnings is not None:
            for k in unknown:
                warnings.append(
                    f"Unknown legacy field '{k}' on entry id="
                    f"{entry_id!r} preserved under metadata.unknown_legacy",
                )
        metadata["unknown_legacy"] = unknown

    return MemoryEntry(
        id=entry_id,
        text=text,
        embedding=embedding,
        scope=scope,
        created_at=created_at,
        source_tool=source_tool,
        metadata=metadata,
    )


def _coerce_scope(value: Any) -> MemoryScope:
    if isinstance(value, MemoryScope):
        return value
    if isinstance(value, str):
        try:
            return MemoryScope(value)
        except ValueError:
            pass
    return MemoryScope.PROJECT


def _coerce_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str) and value:
        try:
            dt = datetime.fromisoformat(value)
        except ValueError:
            dt = datetime.now(timezone.utc)
    else:
        dt = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _entry_to_json(entry: MemoryEntry) -> dict[str, Any]:
    """Render a :class:`MemoryEntry` as a JSON-serialisable dict."""
    return {
        "id": entry.id,
        "text": entry.text,
        "embedding": list(entry.embedding) if entry.embedding is not None else None,
        "scope": entry.scope.value,
        "created_at": entry.created_at.isoformat(),
        "source_tool": entry.source_tool,
        "metadata": entry.metadata,
    }


# The `field` import is kept for API stability even though the current
# MigrationReport defaults do not use it — future additions may.
_ = field
