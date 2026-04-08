"""Centralized LSP language detection: extension table + project-root walker.

This module is the single source of truth for "what language is this file?"
and "where is the project root for this file?". Both `lsp/tools.py` and
`lsp/detector.py` import from here.

Extension table is ported from opencode/packages/opencode/src/lsp/language.ts.
"""
from __future__ import annotations

from pathlib import Path

# Mapping of lowercase extension (with leading dot) -> canonical language id.
# Language ids match LSP `languageId` conventions where possible.
EXTENSION_LANGUAGE: dict[str, str] = {
    # Python
    ".py": "python", ".pyi": "python", ".pyx": "python",
    # JavaScript / TypeScript
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".ts": "typescript", ".tsx": "typescript", ".mts": "typescript", ".cts": "typescript",
    # Web
    ".html": "html", ".htm": "html", ".xhtml": "html",
    ".css": "css", ".scss": "scss", ".sass": "sass", ".less": "less",
    ".vue": "vue", ".svelte": "svelte", ".astro": "astro",
    # Data / config
    ".json": "json", ".jsonc": "jsonc", ".json5": "json5",
    ".yaml": "yaml", ".yml": "yaml",
    ".toml": "toml", ".ini": "ini", ".env": "dotenv",
    ".xml": "xml", ".plist": "xml",
    # Markdown / docs
    ".md": "markdown", ".mdx": "markdown", ".markdown": "markdown",
    ".tex": "latex", ".bib": "bibtex",
    # Shell
    ".sh": "shellscript", ".bash": "shellscript", ".zsh": "shellscript",
    ".fish": "shellscript", ".ksh": "shellscript",
    # Systems
    ".c": "c", ".h": "c",
    ".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp", ".c++": "cpp",
    ".hpp": "cpp", ".hh": "cpp", ".hxx": "cpp", ".h++": "cpp",
    ".rs": "rust",
    ".go": "go",
    ".zig": "zig",
    ".swift": "swift",
    ".m": "objective-c", ".mm": "objective-cpp",
    ".d": "d",
    ".v": "v",
    # JVM
    ".java": "java",
    ".kt": "kotlin", ".kts": "kotlin",
    ".scala": "scala", ".sc": "scala",
    ".groovy": "groovy", ".gradle": "groovy",
    ".clj": "clojure", ".cljs": "clojure", ".cljc": "clojure",
    # .NET
    ".cs": "csharp", ".csx": "csharp",
    ".fs": "fsharp", ".fsi": "fsharp", ".fsx": "fsharp",
    ".vb": "vb",
    # Functional
    ".hs": "haskell", ".lhs": "haskell",
    ".ml": "ocaml", ".mli": "ocaml",
    ".elm": "elm",
    ".erl": "erlang", ".hrl": "erlang",
    ".ex": "elixir", ".exs": "elixir",
    ".purs": "purescript",
    ".rkt": "racket",
    # Scripting
    ".rb": "ruby", ".erb": "ruby",
    ".php": "php", ".phtml": "php",
    ".lua": "lua",
    ".pl": "perl", ".pm": "perl",
    ".r": "r",
    ".jl": "julia",
    ".dart": "dart",
    ".tcl": "tcl",
    # Database / query
    ".sql": "sql", ".psql": "sql", ".mysql": "sql",
    ".graphql": "graphql", ".gql": "graphql",
    # Build / infra
    ".dockerfile": "dockerfile",
    ".tf": "terraform", ".tfvars": "terraform",
    ".hcl": "hcl",
    ".nix": "nix",
    ".bzl": "starlark", ".bazel": "starlark",
    # Other
    ".gleam": "gleam",
    ".nim": "nim",
    ".cr": "crystal",
    ".sol": "solidity",
    ".proto": "proto",
    ".thrift": "thrift",
    ".cmake": "cmake",
    ".mk": "makefile",
}

# Project-root marker filenames searched bottom-up.
# Order matters: a deeper marker takes precedence over a shallower one.
ROOT_MARKERS: tuple[str, ...] = (
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "Pipfile",
    "package.json",
    "tsconfig.json",
    "deno.json",
    "deno.jsonc",
    "go.mod",
    "Cargo.toml",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "build.sbt",
    "mix.exs",
    "Gemfile",
    "composer.json",
    "Project.toml",
    "shard.yml",
    "rebar.config",
    "stack.yaml",
    "dune-project",
    ".git",
)


def language_for_file(file_path: str | Path) -> str:
    """Return the canonical language id for *file_path* or empty string."""
    suffix = Path(str(file_path)).suffix.lower()
    return EXTENSION_LANGUAGE.get(suffix, "")


def find_project_root(file_path: str | Path) -> Path | None:
    """Walk upward from *file_path* searching for any ROOT_MARKER.

    Returns the directory containing the first marker found, or None if
    none is found before the filesystem root.
    """
    try:
        cur = Path(str(file_path)).resolve().parent
    except OSError:
        return None
    while True:
        for marker in ROOT_MARKERS:
            if (cur / marker).exists():
                return cur
        if cur.parent == cur:
            return None
        cur = cur.parent
