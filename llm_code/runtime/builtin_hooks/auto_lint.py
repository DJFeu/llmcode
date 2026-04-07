"""Auto-lint hook: run pyright/mypy on edited Python files."""
from __future__ import annotations

import shutil
import subprocess
from typing import TYPE_CHECKING

from llm_code.runtime.hooks import HookOutcome

if TYPE_CHECKING:
    from llm_code.runtime.hooks import HookRunner

EDIT_TOOLS = {"edit_file", "write_file", "Edit", "Write"}


def _lint(path: str) -> list[str]:
    if not path.endswith(".py"):
        return []
    if shutil.which("pyright"):
        cmd = ["pyright", "--outputjson", path]
    elif shutil.which("mypy"):
        cmd = ["mypy", "--no-error-summary", path]
    else:
        return []
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=15, text=True, check=False)
    except (subprocess.SubprocessError, OSError):
        return []
    if proc.returncode == 0:
        return []
    out = (proc.stdout or proc.stderr).strip()
    if not out:
        return []
    return [f"lint({path}): {out[:300]}"]


def handle(event: str, context: dict) -> HookOutcome | None:
    if context.get("tool_name", "") not in EDIT_TOOLS:
        return None
    path = context.get("file_path", "") or context.get("path", "")
    if not path:
        return None
    return HookOutcome(messages=_lint(path))


def register(hook_runner: "HookRunner") -> None:
    hook_runner.subscribe("post_tool_use", handle)
