"""Repo Map -- AST-based symbol index for codebase overview."""
from __future__ import annotations

import ast
import logging
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_SKIP_DIRS = frozenset({
    ".git", "__pycache__", "node_modules", ".venv", "venv",
    ".tox", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "dist", "build", ".eggs",
})

_PYTHON_EXTS = frozenset({".py", ".pyi"})
_JS_TS_EXTS = frozenset({".js", ".jsx", ".ts", ".tsx"})

_BINARY_EXTS = frozenset({
    ".pyc", ".pyo", ".so", ".dll", ".png", ".jpg", ".jpeg",
    ".gif", ".bmp", ".ico", ".zip", ".gz", ".tar", ".whl",
})


@dataclass(frozen=True)
class ClassSymbol:
    """A class with its public method names."""

    name: str
    methods: tuple[str, ...] = ()


@dataclass(frozen=True)
class FileSymbols:
    """Symbols extracted from a single file."""

    path: str
    classes: tuple[ClassSymbol, ...] = ()
    functions: tuple[str, ...] = ()


@dataclass(frozen=True)
class RepoMap:
    """Immutable collection of per-file symbol summaries."""

    files: tuple[FileSymbols, ...] = ()

    def to_compact(self, max_tokens: int = 2000) -> str:
        """Render a compact text representation of the repo map.

        Stays within approximately *max_tokens* (estimated as chars / 4).
        """
        max_chars = max_tokens * 4
        lines: list[str] = []
        total_chars = 0

        for fs in self.files:
            if not fs.classes and not fs.functions:
                line = fs.path
            else:
                symbols: list[str] = []
                for cls in fs.classes:
                    if cls.methods:
                        symbols.append(f"{cls.name}({', '.join(cls.methods)})")
                    else:
                        symbols.append(cls.name)
                symbols.extend(fs.functions)
                line = f"{fs.path}: {', '.join(symbols)}"

            line_len = len(line) + 1  # +1 for newline
            if total_chars + line_len > max_chars:
                break
            lines.append(line)
            total_chars += line_len

        return "\n".join(lines)


def compute_map_budget(context_window: int, chat_tokens: int) -> int:
    """Compute optimal repo map token budget based on available context."""
    available = context_window - chat_tokens - 4096  # padding
    return min(max(512, available // 8), 4096)


def build_repo_map(cwd: Path, max_files: int = 100) -> RepoMap:
    """Build a symbol map of the repository rooted at *cwd*."""
    source_files: list[Path] = []
    _collect_source_files(cwd, cwd, source_files)
    source_files.sort(key=lambda p: str(p.relative_to(cwd)))

    file_symbols: list[FileSymbols] = []
    for f in source_files[:max_files]:
        rel = str(f.relative_to(cwd))
        file_symbols.append(_parse_file(f, rel))

    return RepoMap(files=tuple(file_symbols))


def _parse_file(path: Path, rel: str) -> FileSymbols:
    """Parse file symbols -- tree-sitter first, fallback to ast/regex."""
    try:
        from llm_code.runtime.treesitter_parser import parse_file as ts_parse

        result = ts_parse(path, rel)
        if result is not None:
            return result
    except Exception:
        pass

    # Existing fallback
    suffix = path.suffix.lower()
    if suffix in _PYTHON_EXTS:
        return _parse_python(path, rel)
    elif suffix in _JS_TS_EXTS:
        return _parse_js_ts(path, rel)
    return FileSymbols(path=rel)


def _collect_source_files(
    base: Path, current: Path, out: list[Path],
) -> None:
    """Recursively collect source files, skipping irrelevant directories."""
    try:
        entries = sorted(current.iterdir(), key=lambda p: p.name)
    except PermissionError:
        return

    for entry in entries:
        if entry.is_dir():
            if entry.name in _SKIP_DIRS or entry.name.startswith("."):
                continue
            _collect_source_files(base, entry, out)
        elif entry.is_file():
            if entry.suffix.lower() in _BINARY_EXTS:
                continue
            try:
                if entry.stat().st_size > 100_000:
                    continue
            except OSError:
                continue
            out.append(entry)


def _parse_python(path: Path, rel_path: str) -> FileSymbols:
    """Parse a Python file using AST to extract classes and functions."""
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source, filename=rel_path)
    except (SyntaxError, OSError):
        return FileSymbols(path=rel_path)

    classes: list[ClassSymbol] = []
    functions: list[str] = []

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.ClassDef):
            methods = tuple(
                item.name
                for item in ast.iter_child_nodes(node)
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                and not item.name.startswith("_")
            )
            classes.append(ClassSymbol(name=node.name, methods=methods))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not node.name.startswith("_"):
                functions.append(node.name)

    return FileSymbols(path=rel_path, classes=tuple(classes), functions=tuple(functions))


def _parse_js_ts(path: Path, rel_path: str) -> FileSymbols:
    """Parse JS/TS file using regex fallback for class/function extraction."""
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return FileSymbols(path=rel_path)

    classes: list[ClassSymbol] = []
    functions: list[str] = []

    for match in re.finditer(r"class\s+(\w+)", source):
        classes.append(ClassSymbol(name=match.group(1)))

    for match in re.finditer(r"(?:export\s+)?function\s+(\w+)", source):
        functions.append(match.group(1))
    for match in re.finditer(r"export\s+const\s+(\w+)", source):
        functions.append(match.group(1))

    return FileSymbols(path=rel_path, classes=tuple(classes), functions=tuple(functions))
