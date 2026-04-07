"""Auto-commit-offer hook: after N edits with auto_commit disabled, suggest /commit."""
from __future__ import annotations

from typing import TYPE_CHECKING

from llm_code.runtime.hooks import HookOutcome

if TYPE_CHECKING:
    from llm_code.runtime.hooks import HookRunner

EDIT_TOOLS = {"edit_file", "write_file", "Edit", "Write"}
EDIT_THRESHOLD = 5

# Module-level counter (per-process). Conversation runtime can reset via reset().
_state: dict[str, int] = {"edits": 0}


def reset() -> None:
    _state["edits"] = 0


def handle(event: str, context: dict) -> HookOutcome | None:
    if context.get("auto_commit", False):
        return None
    if context.get("tool_name", "") not in EDIT_TOOLS:
        return None
    _state["edits"] += 1
    if _state["edits"] < EDIT_THRESHOLD:
        return None
    _state["edits"] = 0
    return HookOutcome(
        messages=[
            f"auto_commit_offer: {EDIT_THRESHOLD} edits made — consider running /commit",
        ]
    )


def register(hook_runner: "HookRunner") -> None:
    hook_runner.subscribe("post_tool_use", handle)
