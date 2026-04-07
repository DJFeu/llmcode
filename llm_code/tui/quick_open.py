"""QuickOpenDialog — Ctrl+P fuzzy file finder.

Pure logic module; the TUI overlay is a thin Textual widget layered on top of
`fuzzy_find_files`. Tests exercise the logic only.
"""
from __future__ import annotations

import difflib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class QuickOpenResult:
    path: str
    preview: str = ""


_IGNORE_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".mypy_cache", ".pytest_cache"}


def _iter_files(root: Path, max_files: int = 5000) -> list[str]:
    """Yield up to max_files repo-relative paths under root."""
    out: list[str] = []
    for p in root.rglob("*"):
        if len(out) >= max_files:
            break
        if not p.is_file():
            continue
        if any(part in _IGNORE_DIRS for part in p.parts):
            continue
        try:
            rel = p.relative_to(root)
        except ValueError:
            continue
        out.append(str(rel))
    return out


def fuzzy_find_files(
    query: str,
    root: Path | str,
    limit: int = 8,
    candidates: list[str] | None = None,
) -> list[QuickOpenResult]:
    """Return up to `limit` fuzzy matches for `query` under `root`.

    If `candidates` supplied, skip filesystem walk (useful for tests).
    Empty query returns the first `limit` candidates.
    """
    root_path = Path(root)
    files = candidates if candidates is not None else _iter_files(root_path)
    if not query:
        return [QuickOpenResult(path=f) for f in files[:limit]]

    q = query.lower()
    # Substring-first ranking, then difflib fallback.
    scored: list[tuple[float, str]] = []
    for f in files:
        fl = f.lower()
        if q in fl:
            # earlier position = higher score
            pos = fl.find(q)
            scored.append((1.0 - pos / max(1, len(fl)), f))
        else:
            # check against basename too for short queries
            base = Path(fl).name
            ratio = max(
                difflib.SequenceMatcher(None, q, fl).ratio(),
                difflib.SequenceMatcher(None, q, base).ratio(),
            )
            if ratio >= 0.3:
                scored.append((ratio * 0.5, f))  # de-prioritize fuzzy vs substring

    scored.sort(key=lambda x: -x[0])
    top = scored[:limit]
    results: list[QuickOpenResult] = []
    for _, f in top:
        preview = ""
        if candidates is None:
            preview = _first_matching_line(root_path / f, query)
        results.append(QuickOpenResult(path=f, preview=preview))
    return results


def _first_matching_line(path: Path, query: str) -> str:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            ql = query.lower()
            for line in fh:
                if ql in line.lower():
                    return line.strip()[:80]
            fh.seek(0)
            first = fh.readline().strip()
            return first[:80]
    except OSError:
        return ""
