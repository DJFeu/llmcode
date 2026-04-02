"""LSP server auto-detector: discovers language servers from project marker files."""
from __future__ import annotations

import shutil
from pathlib import Path

from llm_code.lsp.client import LspServerConfig

# Maps marker filename -> (command, args, language)
_DETECTORS: dict[str, tuple[str, list[str], str]] = {
    "pyproject.toml": ("pyright-langserver", ["--stdio"], "python"),
    "setup.py": ("pyright-langserver", ["--stdio"], "python"),
    "requirements.txt": ("pyright-langserver", ["--stdio"], "python"),
    "package.json": ("typescript-language-server", ["--stdio"], "typescript"),
    "tsconfig.json": ("typescript-language-server", ["--stdio"], "typescript"),
    "go.mod": ("gopls", ["serve"], "go"),
    "Cargo.toml": ("rust-analyzer", [], "rust"),
}


def detect_lsp_servers(cwd: Path) -> dict[str, LspServerConfig]:
    """Detect available LSP servers based on marker files in *cwd*.

    Returns a mapping of language -> LspServerConfig.
    Only includes languages where the server binary exists on PATH.
    If multiple markers for the same language are found, the language
    appears only once.
    """
    found: dict[str, LspServerConfig] = {}
    for marker, (command, args, language) in _DETECTORS.items():
        if language in found:
            continue
        if not (cwd / marker).exists():
            continue
        if shutil.which(command) is None:
            continue
        found[language] = LspServerConfig(
            command=command,
            args=tuple(args),
            language=language,
        )
    return found
