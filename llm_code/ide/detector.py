"""Detect running IDE processes."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class IDEInfo:
    name: str
    pid: int
    workspace_path: str


# Process name patterns -> IDE name
_IDE_PATTERNS: dict[str, str] = {
    "code": "vscode",
    "code-insiders": "vscode",
    "cursor": "vscode",
    "nvim": "neovim",
    "neovim": "neovim",
    "idea": "jetbrains",
    "pycharm": "jetbrains",
    "webstorm": "jetbrains",
    "goland": "jetbrains",
    "clion": "jetbrains",
    "rubymine": "jetbrains",
    "rider": "jetbrains",
    "phpstorm": "jetbrains",
    "datagrip": "jetbrains",
    "subl": "sublime",
    "sublime_text": "sublime",
}


def _iter_processes() -> list:
    """Iterate over running processes. Requires psutil."""
    import psutil  # optional dependency
    return list(psutil.process_iter(["name", "cmdline"]))


def _extract_workspace(cmdline: list[str]) -> str:
    """Best-effort extraction of workspace path from command line args."""
    for arg in reversed(cmdline):
        if arg.startswith("/") and not arg.startswith("--"):
            return arg
    return ""


def detect_running_ide() -> list[IDEInfo]:
    """Scan process list for known IDEs. Returns empty list on failure."""
    try:
        procs = _iter_processes()
    except (ImportError, OSError):
        return []

    results: list[IDEInfo] = []
    for proc in procs:
        try:
            info = proc.info
            name = (info.get("name") or "").lower()
            cmdline = info.get("cmdline") or []
        except (AttributeError, KeyError):
            continue

        ide_name = _IDE_PATTERNS.get(name)
        if ide_name is None:
            continue

        workspace = _extract_workspace(cmdline)
        results.append(IDEInfo(
            name=ide_name,
            pid=proc.pid,
            workspace_path=workspace,
        ))

    return results
