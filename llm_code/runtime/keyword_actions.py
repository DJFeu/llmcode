"""Keyword-driven action detection ported from oh-my-opencode keyword-detector.

Complementary to the skill router: detects predefined keywords in the user's
prompt and maps them to *built-in* actions (mode switches, persona triggers,
shell commands), not skills. Opt-in via ``config.keywords.enabled``.
"""
from __future__ import annotations

KEYWORD_ACTIONS: dict[tuple[str, ...], str] = {
    ("ultrawork", "deep work", "ultra work"): "enable_yolo_mode",
    ("review", "code review"): "trigger_review_persona",
    ("test", "run tests", "pytest"): "run_pytest",
    ("explain", "walk me through"): "trigger_explain_persona",
    ("refactor", "clean up"): "trigger_refactor_persona",
    ("debug", "fix the bug"): "trigger_debug_persona",
}


def detect_action(user_message: str) -> str | None:
    """Return the first matching action name for *user_message*, or None.

    Matching is case-insensitive substring search; the first action whose any
    keyword is contained in the message wins. Order is determined by the
    insertion order of :data:`KEYWORD_ACTIONS`.
    """
    if not user_message:
        return None
    msg = user_message.lower()
    for keywords, action in KEYWORD_ACTIONS.items():
        for kw in keywords:
            if kw in msg:
                return action
    return None


__all__ = ["KEYWORD_ACTIONS", "detect_action"]
