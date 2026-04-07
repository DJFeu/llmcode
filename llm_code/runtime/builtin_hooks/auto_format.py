"""Auto-format hook: run black/ruff on edited Python files after edit_file/write_file."""
from __future__ import annotations

import shutil
import subprocess
from typing import TYPE_CHECKING

from llm_code.runtime.hooks import HookOutcome

if TYPE_CHECKING:
    from llm_code.runtime.hooks import HookRunner

EDIT_TOOLS = {"edit_file", "write_file", "Edit", "Write"}


def _format_file(path: str) -> list[str]:
    msgs: list[str] = []
    if not path.endswith(".py"):
        return msgs
    if shutil.which("ruff"):
        try:
            subprocess.run(
                ["ruff", "format", path], capture_output=True, timeout=10, check=False
            )
            msgs.append(f"ruff format: {path}")
        except (subprocess.SubprocessError, OSError):
            pass
    elif shutil.which("black"):
        try:
            subprocess.run(
                ["black", "-q", path], capture_output=True, timeout=10, check=False
            )
            msgs.append(f"black: {path}")
        except (subprocess.SubprocessError, OSError):
            pass
    return msgs


def handle(event: str, context: dict) -> HookOutcome | None:
    tool = context.get("tool_name", "")
    if tool not in EDIT_TOOLS:
        return None
    path = context.get("file_path", "") or context.get("path", "")
    if not path:
        return None
    return HookOutcome(messages=_format_file(path))


def register(hook_runner: "HookRunner") -> None:
    hook_runner.subscribe("post_tool_use", handle)
