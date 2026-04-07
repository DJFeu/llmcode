"""Context recovery hook: warn at session end if no tools were called."""
from __future__ import annotations

from typing import TYPE_CHECKING

from llm_code.runtime.hooks import HookOutcome

if TYPE_CHECKING:
    from llm_code.runtime.hooks import HookRunner


def handle(event: str, context: dict) -> HookOutcome | None:
    tool_calls = int(context.get("tool_call_count", 0) or 0)
    if tool_calls > 0:
        return None
    return HookOutcome(
        messages=[
            "context_recovery: session ended without any tool calls — "
            "consider whether the agent needed more context.",
        ]
    )


def register(hook_runner: "HookRunner") -> None:
    hook_runner.subscribe("stop", handle)
    hook_runner.subscribe("session_end", handle)
