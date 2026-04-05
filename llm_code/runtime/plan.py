"""Plan mode data structures for presenting tool operations before execution."""
from __future__ import annotations

import dataclasses


@dataclasses.dataclass(frozen=True)
class PlanEntry:
    tool_name: str
    args: dict
    summary: str


@dataclasses.dataclass(frozen=True)
class PlanSummary:
    entries: tuple[PlanEntry, ...]

    def render(self) -> str:
        if not self.entries:
            return "No operations in plan."
        lines = [f"Plan ({len(self.entries)} operations)\n"]
        for i, entry in enumerate(self.entries, 1):
            lines.append(f"  {i}. [{entry.tool_name}] {entry.summary}")
        return "\n".join(lines)


def summarize_tool_call(name: str, args: dict) -> str:
    """Return a human-readable summary of a tool call for plan mode display."""
    if name == "edit_file":
        path = args.get("file_path", "?")
        old = args.get("old_string", "")
        new = args.get("new_string", "")
        old_p = old[:40] + "..." if len(old) > 40 else old
        new_p = new[:40] + "..." if len(new) > 40 else new
        return f"Edit {path}: '{old_p}' -> '{new_p}'"
    if name == "write_file":
        path = args.get("file_path", "?")
        content = args.get("content", "")
        return f"Create {path} ({len(content)} chars)"
    if name == "bash":
        cmd = args.get("command", "?")
        preview = cmd[:60] + "..." if len(cmd) > 60 else cmd
        return f"Run: {preview}"
    params = ", ".join(f"{k}={repr(v)[:30]}" for k, v in list(args.items())[:3])
    return f"{name}({params})"
