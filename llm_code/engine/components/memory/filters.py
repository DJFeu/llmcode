"""Scope / time / metadata filters for memory retrieval (v12 M7 Task 7.6 helper).

These helpers run on the Pipeline side (not the storage backend) so
behaviour is consistent across backends. A backend is free to push the
filter down to its native query language for performance, but the
default path calls :func:`apply_filters` on whatever the backend
returned.

Plan: docs/superpowers/plans/2026-04-21-llm-code-v12-memory-components.md
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

from llm_code.engine.components.memory.schema import MemoryEntry, MemoryScope

__all__ = [
    "apply_filters",
    "filter_by_metadata",
    "filter_by_scope",
    "filter_by_source_tool",
    "filter_by_time",
    "visible_scopes_for",
]


# ---------------------------------------------------------------------------
# Scope visibility policy
# ---------------------------------------------------------------------------
def visible_scopes_for(requested: MemoryScope) -> frozenset[MemoryScope]:
    """Return the set of scopes visible from ``requested``.

    Policy (mirrors the v10 HIDA behaviour described in the spec §5.7):

    - ``SESSION`` sees only session-scoped entries.
    - ``PROJECT`` sees project + global entries.
    - ``GLOBAL`` sees only global-scoped entries.
    """
    if requested is MemoryScope.SESSION:
        return frozenset({MemoryScope.SESSION})
    if requested is MemoryScope.PROJECT:
        return frozenset({MemoryScope.PROJECT, MemoryScope.GLOBAL})
    if requested is MemoryScope.GLOBAL:
        return frozenset({MemoryScope.GLOBAL})
    # Unreachable — ``MemoryScope`` is a closed enum.
    raise ValueError(f"unknown scope: {requested!r}")


# ---------------------------------------------------------------------------
# Individual filters
# ---------------------------------------------------------------------------
def filter_by_scope(
    entries: Iterable[MemoryEntry],
    scope: MemoryScope,
) -> tuple[MemoryEntry, ...]:
    """Keep entries whose scope is visible from ``scope``."""
    visible = visible_scopes_for(scope)
    return tuple(e for e in entries if e.scope in visible)


def filter_by_source_tool(
    entries: Iterable[MemoryEntry],
    source_tool: str,
) -> tuple[MemoryEntry, ...]:
    """Keep entries whose ``source_tool`` matches exactly."""
    return tuple(e for e in entries if e.source_tool == source_tool)


def filter_by_time(
    entries: Iterable[MemoryEntry],
    *,
    after: datetime | str | None = None,
    before: datetime | str | None = None,
) -> tuple[MemoryEntry, ...]:
    """Keep entries within the closed ``[after, before]`` interval.

    ``after`` / ``before`` may be naive — they are promoted to UTC so
    comparisons against ``MemoryEntry.created_at`` (always tz-aware)
    don't raise :class:`TypeError`. An invalid ISO string is treated as
    ``None`` (no bound) so a stale config cannot crash the pipeline.
    """
    a = _coerce_dt(after)
    b = _coerce_dt(before)
    out: list[MemoryEntry] = []
    for e in entries:
        if a is not None and e.created_at < a:
            continue
        if b is not None and e.created_at > b:
            continue
        out.append(e)
    return tuple(out)


def filter_by_metadata(
    entries: Iterable[MemoryEntry],
    criteria: dict[str, Any],
) -> tuple[MemoryEntry, ...]:
    """Keep entries whose metadata equals ``criteria`` for every key.

    Missing keys on an entry count as a no-match (the entry is dropped).
    """
    return tuple(
        e for e in entries
        if all(e.metadata.get(k) == v for k, v in criteria.items())
    )


def apply_filters(
    entries: Iterable[MemoryEntry],
    *,
    scope: MemoryScope | None = None,
    scope_filters: dict[str, Any] | None = None,
) -> tuple[MemoryEntry, ...]:
    """Convenience: apply scope + scope_filters in one call.

    ``scope_filters`` uses the wire-format documented on
    :class:`RetrieverComponent`:

    - ``source_tool``
    - ``created_after`` / ``created_before``
    - ``metadata.<key>``
    """
    result = tuple(entries)
    if scope is not None:
        result = filter_by_scope(result, scope)
    if scope_filters:
        meta_criteria: dict[str, Any] = {}
        for key, expected in scope_filters.items():
            if key == "source_tool":
                result = filter_by_source_tool(result, str(expected))
            elif key == "created_after":
                result = filter_by_time(result, after=expected)
            elif key == "created_before":
                result = filter_by_time(result, before=expected)
            elif key.startswith("metadata."):
                inner = key.split(".", 1)[1]
                meta_criteria[inner] = expected
        if meta_criteria:
            result = filter_by_metadata(result, meta_criteria)
    return result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _coerce_dt(value: datetime | str | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt
