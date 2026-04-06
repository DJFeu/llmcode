"""Sensor implementations — feedback controls that run after tool execution."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from llm_code.harness.config import HarnessFinding
from llm_code.runtime.auto_commit import auto_commit_file
from llm_code.runtime.auto_diagnose import auto_diagnose
from llm_code.analysis.engine import run_analysis


async def lsp_diagnose_sensor(
    lsp_manager: Any | None,
    file_path: str,
) -> list[HarnessFinding]:
    """Run LSP diagnostics on a file. Returns findings or empty list."""
    if lsp_manager is None:
        return []
    try:
        diag_errors = await auto_diagnose(lsp_manager, file_path)
        return [
            HarnessFinding(
                sensor="lsp_diagnose",
                message=msg,
                file_path=file_path,
                severity="error",
            )
            for msg in diag_errors
        ]
    except Exception:
        return []


def code_rules_sensor(cwd: Path, file_path: str) -> list[HarnessFinding]:
    """Run code analysis rules and return findings for the given file."""
    try:
        result = run_analysis(cwd)
        return [
            HarnessFinding(
                sensor="code_rules",
                message=f"{v.rule_key}: {v.message}",
                file_path=v.file_path,
                severity=v.severity,
            )
            for v in result.violations
            if v.file_path == file_path
            or file_path.endswith(v.file_path)
            or v.file_path.endswith(file_path)
        ]
    except Exception:
        return []


def auto_commit_sensor(file_path: Path, tool_name: str) -> HarnessFinding | None:
    """Attempt auto-commit checkpoint. Returns finding on success, None on failure."""
    try:
        ok = auto_commit_file(file_path, tool_name)
        if ok:
            return HarnessFinding(
                sensor="auto_commit",
                message=f"checkpoint: {tool_name} {file_path.name}",
                file_path=str(file_path),
                severity="info",
            )
        return None
    except Exception:
        return None
