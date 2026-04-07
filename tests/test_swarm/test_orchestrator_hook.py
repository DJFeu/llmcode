"""Tests for OrchestratorHook category routing and retry logic."""
from __future__ import annotations

from llm_code.swarm.orchestrator_hook import (
    MAX_RETRIES,
    OrchestratorHook,
    categorize,
    select_persona,
)


def test_categorize_refactor() -> None:
    assert categorize("please refactor this module") == "refactor"


def test_categorize_debug() -> None:
    assert categorize("there's a crash, please fix it") == "debug"


def test_categorize_explain() -> None:
    assert categorize("explain how this function works") == "explain"


def test_categorize_unknown() -> None:
    assert categorize("hello world") == "unknown"


def test_select_persona_skips_attempted() -> None:
    p1 = select_persona("refactor")
    assert p1 is not None
    p2 = select_persona("refactor", attempted=(p1.name,))
    assert p2 is not None and p2.name != p1.name


def test_orchestrate_success_first_try() -> None:
    def executor(persona, task):
        return True, f"done by {persona.name}"

    hook = OrchestratorHook(executor)
    result = hook.orchestrate("explain how foo works")
    assert result.success is True
    assert len(result.attempts) == 1
    assert result.attempts[0].success is True


def test_orchestrate_retries_then_succeeds() -> None:
    calls = {"n": 0}

    def executor(persona, task):
        calls["n"] += 1
        if calls["n"] < 2:
            return False, "boom"
        return True, "ok"

    hook = OrchestratorHook(executor)
    result = hook.orchestrate("refactor this code")
    assert result.success is True
    assert len(result.attempts) == 2
    assert result.attempts[0].persona != result.attempts[1].persona


def test_orchestrate_max_retries_cap() -> None:
    def executor(persona, task):
        return False, "always fails"

    hook = OrchestratorHook(executor, max_retries=3)
    result = hook.orchestrate("debug crash")
    assert result.success is False
    assert len(result.attempts) <= MAX_RETRIES


def test_orchestrate_accumulates_context() -> None:
    seen_tasks: list[str] = []

    def executor(persona, task):
        seen_tasks.append(task)
        return False, "no go"

    hook = OrchestratorHook(executor, max_retries=3)
    hook.orchestrate("refactor this")
    assert len(seen_tasks) >= 2
    assert "previous attempt" in seen_tasks[1]
