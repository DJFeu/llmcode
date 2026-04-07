"""Intent classifier hook: tag UserPromptSubmit events with a coarse intent label."""
from __future__ import annotations

from typing import TYPE_CHECKING

from llm_code.runtime.hooks import HookOutcome

if TYPE_CHECKING:
    from llm_code.runtime.hooks import HookRunner

INTENT_KEYWORDS: dict[str, tuple[str, ...]] = {
    "build": ("build", "implement", "create", "add", "scaffold", "ship"),
    "explain": ("explain", "what is", "how does", "why", "describe"),
    "refactor": ("refactor", "clean up", "rename", "extract", "simplify"),
    "debug": ("debug", "fix", "broken", "error", "crash", "bug"),
    "test": ("test", "pytest", "unit test", "coverage"),
    "review": ("review", "audit", "lint"),
}


def classify(prompt: str) -> str:
    p = (prompt or "").lower()
    for intent, words in INTENT_KEYWORDS.items():
        if any(w in p for w in words):
            return intent
    return "unknown"


def handle(event: str, context: dict) -> HookOutcome | None:
    prompt = context.get("prompt", "") or context.get("message", "")
    if not prompt:
        return None
    intent = classify(prompt)
    # Stash on session if available
    session = context.get("session")
    if session is not None:
        try:
            setattr(session, "last_intent", intent)
        except Exception:
            pass
    return HookOutcome(messages=[f"intent={intent}"])


def register(hook_runner: "HookRunner") -> None:
    hook_runner.subscribe("prompt_submit", handle)
