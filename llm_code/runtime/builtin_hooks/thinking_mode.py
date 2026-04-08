"""Thinking-mode trigger hook (ported from oh-my-opencode/think-mode)."""
from __future__ import annotations

from typing import TYPE_CHECKING

from llm_code.runtime.hooks import HookOutcome

if TYPE_CHECKING:
    from llm_code.runtime.hooks import HookRunner

TRIGGERS_EN: tuple[str, ...] = (
    "ultrathink",
    "think harder",
    "think hard",
    "think deeply",
    "deep think",
)
TRIGGERS_CJK: tuple[str, ...] = (
    "深入思考",
    "仔細想",
    "仔细想",
    "深思",
)

_SESSION_REQUESTS: set[str] = set()


def _matches(prompt: str) -> bool:
    if not prompt:
        return False
    lowered = prompt.lower()
    if any(t in lowered for t in TRIGGERS_EN):
        return True
    return any(t in prompt for t in TRIGGERS_CJK)


def handle(event: str, context: dict) -> HookOutcome | None:
    if event == "session_end":
        sid = context.get("session_id", "")
        if sid:
            _SESSION_REQUESTS.discard(sid)
        return None

    prompt = context.get("prompt", "") or ""
    if not _matches(prompt):
        return None

    sid = context.get("session_id", "")
    if sid:
        _SESSION_REQUESTS.add(sid)
    context["thinking_requested"] = True
    return HookOutcome(messages=[f"thinking_mode: triggered for session={sid or '?'}"])


def was_requested(session_id: str) -> bool:
    return session_id in _SESSION_REQUESTS


def register(hook_runner: "HookRunner") -> None:
    hook_runner.subscribe("prompt_submit", handle)
    hook_runner.subscribe("session_end", handle)
