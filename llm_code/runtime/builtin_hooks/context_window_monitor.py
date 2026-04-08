"""Context window usage warning hook (ported from oh-my-opencode)."""
from __future__ import annotations

from typing import TYPE_CHECKING

from llm_code.runtime.hooks import HookOutcome

if TYPE_CHECKING:
    from llm_code.runtime.hooks import HookRunner

WARN_THRESHOLD = 0.75

_WARNED_SESSIONS: set[str] = set()


def _usage_pct(context: dict) -> float | None:
    used = int(context.get("tokens_used", 0) or 0)
    mx = int(context.get("tokens_max", 0) or 0)
    if mx <= 0:
        return None
    return used / mx


def handle(event: str, context: dict) -> HookOutcome | None:
    if event == "session_end":
        sid = context.get("session_id", "")
        if sid:
            _WARNED_SESSIONS.discard(sid)
        return None

    sid = context.get("session_id", "")
    if not sid or sid in _WARNED_SESSIONS:
        return None

    pct = _usage_pct(context)
    if pct is None or pct < WARN_THRESHOLD:
        return None

    _WARNED_SESSIONS.add(sid)
    pct_int = int(round(pct * 100))
    banner = f"\n\n[Context Status: {pct_int}% used — consider /compact or wrapping up.]"
    return HookOutcome(extra_output=banner, messages=[f"context_window_monitor: {pct_int}%"])


def register(hook_runner: "HookRunner") -> None:
    hook_runner.subscribe("post_tool_use", handle)
    hook_runner.subscribe("session_end", handle)
