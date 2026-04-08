"""Smoke test: thinking_mode hook is invoked from prompt_submit fire site."""
from __future__ import annotations

from llm_code.runtime.builtin_hooks import thinking_mode
from llm_code.runtime.hooks import HookRunner


def test_fire_python_prompt_submit_triggers_thinking_mode() -> None:
    runner = HookRunner()
    thinking_mode.register(runner)
    ctx = {"prompt": "ultrathink please", "session_id": "s1"}
    runner.fire_python("prompt_submit", ctx)
    assert thinking_mode.was_requested("s1") is True
    thinking_mode._SESSION_REQUESTS.clear()
