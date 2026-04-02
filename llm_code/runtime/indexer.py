"""Project file and symbol indexer."""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FileEntry:
    path: str       # relative path from project root
    size: int
    language: str   # "python", "typescript", "go", "rust", "javascript", "unknown"


@dataclass(frozen=True)
class SymbolEntry:
    name: str
    kind: str       # "class" | "function" | "method" | "variable" | "export"
    file: str       # relative path
    line: int


@dataclass(frozen=True)
class ProjectIndex:
    files: tuple[FileEntry, ...]
    symbols: tuple[SymbolEntry, ...]
    generated_at: str


# ---------------------------------------------------------------------------
# Language detection
# ---------------------------------------------------------------------------

_EXT_TO_LANG: dict[str, str] = {
    ".py": "python",
    ".pyi": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".rb": "ruby",
    ".php": "php",
    ".swift": "swift",
    ".kt": "kotlin",
}

# ---------------------------------------------------------------------------
# Directories to skip
# ---------------------------------------------------------------------------

_SKIP_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        "node_modules",
        ".venv",
        "venv",
        "__pycache__",
        "dist",
        "build",
        ".next",
        ".nuxt",
        "target",
        ".tox",
        ".mypy_cache",
        ".pytest_cache",
    }
)

# ---------------------------------------------------------------------------
# Symbol regex patterns
# ---------------------------------------------------------------------------

_SYMBOL_PATTERNS: dict[str, list[tuple[re.Pattern[str], str]]] = {
    "python": [
        (re.compile(r"^class\s+(\w+)"), "class"),
        (re.compile(r"^def\s+(\w+)"), "function"),
        (re.compile(r"^(\w+)\s*(?::\s*\w+)?\s*="), "variable"),
    ],
    "typescript": [
        (re.compile(r"^export\s+(?:class|interface)\s+(\w+)"), "class"),
        (re.compile(r"^export\s+(?:function|const|let|var)\s+(\w+)"), "export"),
        (re.compile(r"^class\s+(\w+)"), "class"),
        (re.compile(r"^function\s+(\w+)"), "function"),
    ],
    "javascript": [
        (re.compile(r"^export\s+(?:class|function|const|let|var)\s+(\w+)"), "export"),
        (re.compile(r"^class\s+(\w+)"), "class"),
        (re.compile(r"^function\s+(\w+)"), "function"),
    ],
    "go": [
        (re.compile(r"^func\s+(\w+)"), "function"),
        (re.compile(r"^type\s+(\w+)\s+struct"), "class"),
        (re.compile(r"^type\s+(\w+)\s+interface"), "class"),
    ],
    "rust": [
        (re.compile(r"^(?:pub\s+)?fn\s+(\w+)"), "function"),
        (re.compile(r"^(?:pub\s+)?struct\s+(\w+)"), "class"),
        (re.compile(r"^(?:pub\s+)?enum\s+(\w+)"), "class"),
        (re.compile(r"^(?:pub\s+)?trait\s+(\w+)"), "class"),
    ],
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _should_skip(name: str) -> bool:
    """Return True if the directory name matches a skip pattern."""
    if name in _SKIP_DIRS:
        return True
    # Handle glob-style patterns like *.egg-info
    if name.endswith(".egg-info"):
        return True
    return False


def _detect_language(path: Path) -> str:
    return _EXT_TO_LANG.get(path.suffix.lower(), "unknown")


# ---------------------------------------------------------------------------
# ProjectIndexer
# ---------------------------------------------------------------------------


class ProjectIndexer:
    def __init__(self, cwd: Path) -> None:
        self._cwd = cwd

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build_index(self) -> ProjectIndex:
        files = self._scan_files()
        symbols: list[SymbolEntry] = []
        for f in files:
            symbols.extend(self._extract_symbols(f))
        now = datetime.now(timezone.utc).isoformat()
        return ProjectIndex(
            files=tuple(files),
            symbols=tuple(symbols),
            generated_at=now,
        )

    def save(self, index: ProjectIndex, path: Path) -> None:
        data = {
            "files": [asdict(f) for f in index.files],
            "symbols": [asdict(s) for s in index.symbols],
            "generated_at": index.generated_at,
        }
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    @staticmethod
    def load(path: Path) -> ProjectIndex | None:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            files = tuple(FileEntry(**f) for f in raw["files"])
            symbols = tuple(SymbolEntry(**s) for s in raw["symbols"])
            return ProjectIndex(
                files=files,
                symbols=symbols,
                generated_at=raw["generated_at"],
            )
        except (FileNotFoundError, KeyError, TypeError, json.JSONDecodeError):
            return None

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _scan_files(self) -> list[FileEntry]:
        entries: list[FileEntry] = []
        for item in self._walk(self._cwd):
            rel = item.relative_to(self._cwd).as_posix()
            entries.append(
                FileEntry(
                    path=rel,
                    size=item.stat().st_size,
                    language=_detect_language(item),
                )
            )
        entries.sort(key=lambda e: e.path)
        return entries

    def _walk(self, root: Path):
        """Yield all files under *root*, skipping ignored directories."""
        for child in sorted(root.iterdir()):
            if child.is_dir():
                if not _should_skip(child.name):
                    yield from self._walk(child)
            elif child.is_file():
                yield child

    def _extract_symbols(self, file: FileEntry) -> list[SymbolEntry]:
        patterns = _SYMBOL_PATTERNS.get(file.language)
        if not patterns:
            return []
        abs_path = self._cwd / file.path
        try:
            text = abs_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return []
        results: list[SymbolEntry] = []
        for lineno, line in enumerate(text.splitlines(), start=1):
            for pattern, kind in patterns:
                m = pattern.match(line)
                if m:
                    results.append(
                        SymbolEntry(
                            name=m.group(1),
                            kind=kind,
                            file=file.path,
                            line=lineno,
                        )
                    )
                    break  # first matching pattern wins for this line
        return results
