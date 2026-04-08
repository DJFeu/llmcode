"""LSP server auto-detector: discovers language servers from project markers.

Two entry points:

* :func:`detect_lsp_servers` — given a directory, return the language servers
  whose marker files exist *in that directory* and whose binaries are on PATH.
  This is the legacy API used at session startup with the project cwd.

* :func:`detect_lsp_servers_for_file` — given a file path, walk upward to find
  the project root (via :func:`find_project_root`) and then call
  :func:`detect_lsp_servers` on that root. Use this when you discover the LSP
  needs lazily based on which file the user is touching.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from llm_code.lsp.client import LspServerConfig
from llm_code.lsp.languages import find_project_root


# Registry of (language id -> (command, args, marker filenames)).
# Marker filenames are tried in order; the first one that exists triggers the
# server. Multiple markers per language let us cover both monorepo styles.
SERVER_REGISTRY: dict[str, tuple[str, list[str], tuple[str, ...]]] = {
    "python": (
        "pyright-langserver", ["--stdio"],
        ("pyproject.toml", "setup.py", "setup.cfg", "Pipfile", "requirements.txt"),
    ),
    "typescript": (
        "typescript-language-server", ["--stdio"],
        ("package.json", "tsconfig.json"),
    ),
    "deno": ("deno", ["lsp"], ("deno.json", "deno.jsonc")),
    "go": ("gopls", ["serve"], ("go.mod",)),
    "rust": ("rust-analyzer", [], ("Cargo.toml",)),
    "ruby": ("solargraph", ["stdio"], ("Gemfile", ".solargraph.yml")),
    "java": ("jdtls", [], ("pom.xml", "build.gradle", "build.gradle.kts")),
    "kotlin": (
        "kotlin-language-server", [],
        ("build.gradle.kts", "build.gradle", "settings.gradle.kts"),
    ),
    "scala": ("metals", [], ("build.sbt", "build.sc")),
    "haskell": ("haskell-language-server-wrapper", ["--lsp"], ("stack.yaml", "cabal.project", "*.cabal")),
    "lua": ("lua-language-server", [], (".luarc.json", ".luarc.jsonc")),
    "zig": ("zls", [], ("build.zig",)),
    "elixir": ("elixir-ls", [], ("mix.exs",)),
    "erlang": ("erlang_ls", [], ("rebar.config",)),
    "ocaml": ("ocamllsp", [], ("dune-project", "*.opam")),
    "swift": ("sourcekit-lsp", [], ("Package.swift",)),
    "cpp": ("clangd", [], ("compile_commands.json", "CMakeLists.txt", ".clangd")),
    "c": ("clangd", [], ("compile_commands.json", "CMakeLists.txt", ".clangd")),
    "csharp": ("OmniSharp", ["-lsp"], ("*.csproj", "*.sln")),
    "html": ("vscode-html-language-server", ["--stdio"], ("package.json",)),
    "css": ("vscode-css-language-server", ["--stdio"], ("package.json",)),
    "json": ("vscode-json-language-server", ["--stdio"], ("package.json",)),
    "yaml": ("yaml-language-server", ["--stdio"], (".github", ".gitlab-ci.yml")),
    "vue": ("vue-language-server", ["--stdio"], ("package.json",)),
    "gleam": ("gleam", ["lsp"], ("gleam.toml",)),
    "nim": ("nimlsp", [], ("*.nimble",)),
}


def _marker_present(directory: Path, marker: str) -> bool:
    """Check whether *marker* exists in *directory* (supports glob patterns)."""
    if any(ch in marker for ch in "*?["):
        return any(directory.glob(marker))
    return (directory / marker).exists()


def detect_lsp_servers(cwd: Path) -> dict[str, LspServerConfig]:
    """Detect language servers whose markers exist directly in *cwd*.

    Returns a mapping language -> :class:`LspServerConfig`. Languages whose
    binary is not on PATH are silently skipped so a missing server never
    breaks startup.
    """
    found: dict[str, LspServerConfig] = {}
    for language, (command, args, markers) in SERVER_REGISTRY.items():
        if language in found:
            continue
        if not any(_marker_present(cwd, m) for m in markers):
            continue
        if shutil.which(command) is None:
            continue
        found[language] = LspServerConfig(
            command=command,
            args=tuple(args),
            language=language,
        )
    return found


def detect_lsp_servers_for_file(file_path: Path | str) -> dict[str, LspServerConfig]:
    """Walk up from *file_path* to its project root, then run detect_lsp_servers."""
    root = find_project_root(file_path)
    if root is None:
        return {}
    return detect_lsp_servers(root)
