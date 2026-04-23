"""Diagnostics — collect unsupported-pattern reports from rewriters.

Rewriters are forbidden to silently skip a shape they cannot handle.
Instead they call :meth:`Diagnostics.report` with a ``pattern`` tag, a
``path`` + ``line`` location, and an optional human-friendly
``suggestion``. :class:`Diagnostics` accumulates these, groups them by
pattern, and renders a text report for the CLI or a JSON report for
``--report FILE`` consumption.

Design:

- Pure data: the class holds a list of :class:`DiagnosticEntry` records.
- :meth:`render_text` groups by pattern and prints file:line samples
  (up to ``max_samples_per_pattern`` per group).
- :meth:`to_json` round-trips through stdlib ``json``; no custom
  encoder required.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class DiagnosticEntry:
    """One unsupported-pattern occurrence reported by a rewriter.

    Attributes:
        pattern: A rewriter-defined pattern identifier, e.g.
            ``"metaprogramming_on_self_class"``. Used as the grouping
            key in :meth:`Diagnostics.render_text`.
        path: Absolute or repo-relative path to the source file.
        line: 1-indexed line number of the offending syntax (or ``0``
            when the rewriter cannot pinpoint a line).
        rewriter: Name of the rewriter that emitted the diagnostic.
        suggestion: Human-readable guidance. Should direct the reader
            to a specific manual-migration section in
            ``docs/plugin_migration_guide.md``.
    """

    pattern: str
    path: str
    line: int
    rewriter: str
    suggestion: str = ""


@dataclass
class Diagnostics:
    """Accumulator for :class:`DiagnosticEntry` records."""

    entries: list[DiagnosticEntry] = field(default_factory=list)

    def report(
        self,
        *,
        pattern: str,
        path: str | Path,
        line: int,
        rewriter: str,
        suggestion: str = "",
    ) -> None:
        """Record one unsupported-pattern occurrence.

        ``path`` is normalised to ``str`` so later JSON serialisation is
        trivial.
        """
        self.entries.append(
            DiagnosticEntry(
                pattern=pattern,
                path=str(path),
                line=line,
                rewriter=rewriter,
                suggestion=suggestion,
            )
        )

    def extend(self, other: "Diagnostics") -> None:
        """Merge another :class:`Diagnostics` into this one."""
        self.entries.extend(other.entries)

    def any(self) -> bool:
        """True iff at least one diagnostic has been reported."""
        return bool(self.entries)

    def group_by_pattern(self) -> dict[str, list[DiagnosticEntry]]:
        """Return a dict keyed by :attr:`DiagnosticEntry.pattern`.

        Insertion order of the keys follows first-occurrence order so
        rendering is deterministic per run.
        """
        buckets: dict[str, list[DiagnosticEntry]] = {}
        for entry in self.entries:
            buckets.setdefault(entry.pattern, []).append(entry)
        return buckets

    def render_text(self, *, max_samples_per_pattern: int = 5) -> str:
        """Human-friendly grouped report.

        Returns an empty string when no diagnostics have been reported,
        so callers can use it as a boolean-ish value.
        """
        if not self.entries:
            return ""
        lines: list[str] = ["Unsupported patterns detected:"]
        for pattern, group in self.group_by_pattern().items():
            lines.append(f"  [{pattern}] — {len(group)} occurrence(s)")
            for entry in group[:max_samples_per_pattern]:
                lines.append(
                    f"    {entry.path}:{entry.line}  ({entry.rewriter})"
                )
                if entry.suggestion:
                    lines.append(f"      hint: {entry.suggestion}")
            extra = len(group) - max_samples_per_pattern
            if extra > 0:
                lines.append(f"    ... and {extra} more")
        return "\n".join(lines) + "\n"

    def to_json(self) -> str:
        """JSON payload for ``--report FILE`` output.

        Shape::

            {
              "count": <int>,
              "entries": [ {pattern, path, line, rewriter, suggestion}, ... ]
            }
        """
        return json.dumps(
            {
                "count": len(self.entries),
                "entries": [asdict(e) for e in self.entries],
            },
            indent=2,
            sort_keys=True,
        )

    def write_json(self, path: str | Path) -> None:
        """Serialise :meth:`to_json` to ``path``."""
        Path(path).write_text(self.to_json(), encoding="utf-8")
