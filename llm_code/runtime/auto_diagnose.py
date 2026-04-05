"""Auto-diagnose -- run LSP diagnostics after file edits and report errors."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Extension to language mapping (mirrors llm_code/lsp/tools.py)
_EXT_LANGUAGE: dict[str, str] = {
    ".py": "python",
    ".pyi": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "typescript",
    ".jsx": "typescript",
    ".go": "go",
    ".rs": "rust",
}


def format_diagnostics(diagnostics: list[Any]) -> list[str]:
    """Format diagnostic objects into human-readable strings."""
    return [
        f"{d.file}:{d.line}:{d.column} [{d.severity}] {d.message} ({d.source})"
        for d in diagnostics
    ]


async def auto_diagnose(lsp_manager: Any, file_path: str) -> list[str]:
    """Run LSP diagnostics on a file and return error-level issues only.

    Returns a list of formatted error strings. Empty list if no errors
    or LSP is unavailable. Never raises — all exceptions are caught.
    """
    try:
        suffix = Path(file_path).suffix.lower()
        language = _EXT_LANGUAGE.get(suffix, "")
        if not language:
            return []

        client = lsp_manager.get_client(language)
        if client is None:
            return []

        file_uri = Path(file_path).resolve().as_uri()
        diagnostics = await client.get_diagnostics(file_uri)

        if not diagnostics:
            return []

        # Filter to error-level only
        errors = [d for d in diagnostics if d.severity == "error"]
        if not errors:
            return []

        return format_diagnostics(errors)

    except Exception:
        logger.debug("Auto-diagnose failed for %s", file_path, exc_info=True)
        return []
