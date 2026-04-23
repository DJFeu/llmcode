"""Runner — walk a plugin source tree and drive the rewriters.

Public surface:

* :class:`RunResult` — immutable summary of a run (for CLI display /
  tests).
* :func:`run` — main entry point. Accepts a root path, a list of
  rewriter names (or ``None`` for all), dry-run toggle, and an optional
  pre-constructed :class:`Diagnostics` accumulator.

The runner never raises on per-file parse failures; it records them as
diagnostics (``pattern="python_parse_error"`` /
``"pyproject_parse_error"``) and continues. This matches the plan's
"never silently skip" requirement: every unexpected shape becomes a
diagnostic the caller sees.

Idempotence contract: a second :func:`run` on the same tree must yield
zero mutations. The runner short-circuits when the rewritten source is
byte-identical to the original; the diff list stays empty in that case.
"""
from __future__ import annotations

import difflib
import fnmatch
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import libcst as cst

from llm_code.migrate.v12.diagnostics import Diagnostics
from llm_code.migrate.v12.rewriters import (
    ALL_REWRITERS,
    PYPROJECT_REWRITERS,
    PYTHON_REWRITERS,
)

DEFAULT_GITIGNORE_PATTERNS = (
    ".venv",
    ".venv*",
    "venv",
    "__pycache__",
    ".git",
    "build",
    "dist",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
    "node_modules",
)

# Path-prefix excludes — matched against the relative posix path
# (every segment joined by ``/``). Patterns are plain string
# prefixes; wildcards are NOT expanded here on purpose so the
# behaviour is trivially auditable. Use this list to keep the codemod
# from rewriting its OWN fixtures when the tool is run against the
# llmcode repo itself (self-migration sanity check).
DEFAULT_EXCLUDED_PATH_PREFIXES = (
    # The codemod's own test fixtures: deliberately written as
    # "legacy" plugin source so the rewriter suites have before/after
    # pairs to assert against. They must NEVER be rewritten in place.
    "tests/test_migrate/fixtures/",
    "test_migrate/fixtures/",
    # Generic test-fixture directories that frequently contain
    # intentionally-outdated sample code (user's own tests + llmcode's
    # shared fixtures).
    "tests/fixtures/",
    # Snapshot / golden files: treated as read-only by convention.
    "tests/__snapshots__/",
    "tests/snapshots/",
    # Docs + spec source: markdown / rst should never be touched by a
    # Python source rewriter, but doubly guard in case a doc file
    # carries a ``pyproject.toml`` example we don't want edited.
    "docs/",
)


@dataclass(frozen=True)
class FileChange:
    """Per-file record used by the CLI and tests."""

    path: str
    diff: str
    mutated: bool
    rewriters_applied: tuple[str, ...]


@dataclass
class RunResult:
    """Summary of a :func:`run` invocation."""

    root: str
    dry_run: bool
    rewriters: tuple[str, ...]
    files_seen: int = 0
    files_changed: int = 0
    changes: list[FileChange] = field(default_factory=list)
    diagnostics: Diagnostics = field(default_factory=Diagnostics)

    def unified_diff(self) -> str:
        """Join every :attr:`FileChange.diff` into a single text blob."""
        parts = [c.diff for c in self.changes if c.diff]
        return "".join(parts)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run(
    root: str | Path,
    *,
    rewriters: Iterable[str] | None = None,
    dry_run: bool = False,
    diagnostics: Diagnostics | None = None,
) -> RunResult:
    """Rewrite every ``.py`` + ``pyproject.toml`` under ``root``.

    Args:
        root: Root of the plugin source tree. Must exist.
        rewriters: Subset of rewriter names to run (default: all).
        dry_run: If True, do not write anything; the rewritten sources
            are discarded after the diff is captured.
        diagnostics: Pre-created diagnostics accumulator — pass one in
            if you want to merge multiple runs.

    Returns:
        A :class:`RunResult` with per-file changes and the populated
        diagnostics object.

    Raises:
        FileNotFoundError: if ``root`` does not exist.
        ValueError: if any requested rewriter name is unknown.
    """
    root_path = Path(root)
    if not root_path.exists():
        raise FileNotFoundError(f"migrate root does not exist: {root}")

    selected = _resolve_rewriters(rewriters)
    result = RunResult(
        root=str(root_path),
        dry_run=dry_run,
        rewriters=selected,
        diagnostics=diagnostics or Diagnostics(),
    )

    for py_file in _iter_python_files(root_path):
        _process_python_file(py_file, selected, dry_run, result)

    for toml_file in _iter_pyproject_files(root_path):
        _process_pyproject(toml_file, selected, dry_run, result)

    return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_rewriters(rewriters: Iterable[str] | None) -> tuple[str, ...]:
    if rewriters is None:
        return ALL_REWRITERS
    names = tuple(rewriters)
    unknown = [n for n in names if n not in ALL_REWRITERS]
    if unknown:
        raise ValueError(f"unknown rewriter(s): {unknown!r}")
    return names


def _iter_python_files(root: Path) -> Iterable[Path]:
    if root.is_file():
        if root.suffix == ".py":
            yield root
        return
    for path in sorted(root.rglob("*.py")):
        if _is_excluded(path, root):
            continue
        yield path


def _iter_pyproject_files(root: Path) -> Iterable[Path]:
    if root.is_file():
        if root.name == "pyproject.toml":
            yield root
        return
    for path in sorted(root.rglob("pyproject.toml")):
        if _is_excluded(path, root):
            continue
        yield path


def _is_excluded(path: Path, root: Path) -> bool:
    rel = path.relative_to(root) if path != root else Path(path.name)

    # Prefix match against the full relative path (posix-style). Catches
    # multi-segment paths like ``tests/test_migrate/fixtures/...`` that
    # a per-component match cannot express.
    rel_posix = "/".join(rel.parts)
    for prefix in DEFAULT_EXCLUDED_PATH_PREFIXES:
        if rel_posix.startswith(prefix):
            return True

    # Per-component match for .venv / __pycache__ / etc.
    for part in rel.parts:
        for pattern in DEFAULT_GITIGNORE_PATTERNS:
            if fnmatch.fnmatch(part, pattern):
                return True
    return False


def _process_python_file(
    path: Path,
    selected: tuple[str, ...],
    dry_run: bool,
    result: RunResult,
) -> None:
    result.files_seen += 1
    original = path.read_text(encoding="utf-8")

    try:
        module = cst.parse_module(original)
    except cst.ParserSyntaxError as exc:
        result.diagnostics.report(
            pattern="python_parse_error",
            path=str(path),
            line=getattr(exc, "raw_line", 0) or 0,
            rewriter="runner",
            suggestion=(
                "libcst failed to parse the file; make sure it contains "
                "valid Python."
            ),
        )
        return

    rewriters_applied: list[str] = []
    for name in selected:
        factory = PYTHON_REWRITERS.get(name)
        if factory is None:
            continue
        transformer, local_diag = factory()
        if hasattr(transformer, "set_path"):
            transformer.set_path(str(path))
        module = module.visit(transformer)
        if local_diag.any():
            result.diagnostics.extend(local_diag)
        rewriters_applied.append(name)

    rewritten = module.code
    if rewritten == original:
        return

    diff = _unified_diff(str(path), original, rewritten)
    result.files_changed += 1
    result.changes.append(
        FileChange(
            path=str(path),
            diff=diff,
            mutated=True,
            rewriters_applied=tuple(rewriters_applied),
        )
    )
    if not dry_run:
        path.write_text(rewritten, encoding="utf-8")


def _process_pyproject(
    path: Path,
    selected: tuple[str, ...],
    dry_run: bool,
    result: RunResult,
) -> None:
    result.files_seen += 1
    original = path.read_text(encoding="utf-8")
    rewritten = original
    rewriters_applied: list[str] = []
    for name in selected:
        rewriter = PYPROJECT_REWRITERS.get(name)
        if rewriter is None:
            continue
        rewritten = rewriter(rewritten, str(path), result.diagnostics)
        rewriters_applied.append(name)

    if rewritten == original:
        return

    diff = _unified_diff(str(path), original, rewritten)
    result.files_changed += 1
    result.changes.append(
        FileChange(
            path=str(path),
            diff=diff,
            mutated=True,
            rewriters_applied=tuple(rewriters_applied),
        )
    )
    if not dry_run:
        path.write_text(rewritten, encoding="utf-8")


def _unified_diff(path: str, before: str, after: str) -> str:
    lines = list(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            n=3,
        )
    )
    return "".join(lines)
