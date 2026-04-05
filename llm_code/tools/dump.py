"""DAFC Dump -- concatenate repo source files for external LLM consumption."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

_SKIP_DIRS = frozenset({
    ".git", "__pycache__", "node_modules", ".venv", "venv",
    ".tox", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "dist", "build", ".eggs",
})

_SKIP_EXTENSIONS = frozenset({
    ".pyc", ".pyo", ".so", ".dll", ".dylib", ".exe",
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg",
    ".woff", ".woff2", ".ttf", ".eot",
    ".zip", ".tar", ".gz", ".bz2", ".xz",
    ".db", ".sqlite", ".sqlite3",
    ".bin", ".dat",
})

_MAX_SINGLE_FILE_BYTES = 50_000  # 50KB
_MAX_TOTAL_BYTES = 500_000       # 500KB


@dataclass(frozen=True)
class DumpResult:
    text: str
    file_count: int
    total_lines: int
    estimated_tokens: int


def dump_codebase(
    cwd: Path,
    max_files: int = 200,
    max_file_size: int = _MAX_SINGLE_FILE_BYTES,
    max_total_size: int = _MAX_TOTAL_BYTES,
) -> DumpResult:
    """Walk cwd, concatenate source files into a single text dump.

    Skips binary files, large files, and common non-source directories.
    """
    files: list[Path] = []
    _collect_files(cwd, cwd, files, max_files, max_file_size)
    files.sort(key=lambda p: str(p.relative_to(cwd)))

    parts: list[str] = []
    total_lines = 0
    total_bytes = 0

    for f in files:
        if total_bytes >= max_total_size:
            break
        try:
            content = f.read_text(encoding="utf-8", errors="strict")
        except (UnicodeDecodeError, OSError):
            continue  # skip binary / unreadable

        rel_path = str(f.relative_to(cwd))
        total_lines += content.count("\n") + (1 if content and not content.endswith("\n") else 0)
        total_bytes += len(content.encode("utf-8"))
        parts.append(f"--- file: {rel_path} ---\n{content}\n")

    text = "".join(parts)
    file_count = len(parts)

    return DumpResult(
        text=text,
        file_count=file_count,
        total_lines=total_lines,
        estimated_tokens=len(text) // 4,
    )


def _collect_files(
    base: Path,
    current: Path,
    out: list[Path],
    limit: int,
    max_file_size: int,
) -> None:
    """Recursively collect files, respecting skip rules and limits."""
    if len(out) >= limit:
        return

    try:
        entries = sorted(current.iterdir(), key=lambda p: p.name)
    except PermissionError:
        return

    for entry in entries:
        if len(out) >= limit:
            return

        if entry.is_dir():
            # Skip known non-source dirs and hidden dirs
            if entry.name in _SKIP_DIRS or entry.name.startswith("."):
                continue
            # Skip egg-info directories (*.egg-info pattern)
            if entry.name.endswith(".egg-info"):
                continue
            _collect_files(base, entry, out, limit, max_file_size)
        elif entry.is_file():
            if entry.suffix.lower() in _SKIP_EXTENSIONS:
                continue
            if entry.stat().st_size > max_file_size:
                continue
            # Quick binary check: look for null bytes in first 512 bytes
            try:
                head = entry.read_bytes()[:512]
                if b"\x00" in head:
                    continue  # likely binary
            except OSError:
                continue
            out.append(entry)
