"""Tests for the builtin hook implementations."""
from __future__ import annotations

from llm_code.runtime import builtin_hooks
from llm_code.runtime.builtin_hooks import (
    auto_commit_offer,
    auto_format,
    auto_lint,
    context_recovery,
    intent_classifier,
)
from llm_code.runtime.hooks import HookRunner


def test_register_all_subscribes_every_builtin() -> None:
    runner = HookRunner()
    builtin_hooks.register_all(runner)
    assert runner._subscribers, "expected subscribers to be populated"


def test_register_named_only_registers_subset() -> None:
    runner = HookRunner()
    registered = builtin_hooks.register_named(runner, ("auto_format", "unknown"))
    assert registered == ["auto_format"]


def test_auto_format_ignores_non_python() -> None:
    out = auto_format.handle(
        "post_tool_use", {"tool_name": "edit_file", "file_path": "README.md"}
    )
    assert out is not None and out.messages == []


def test_auto_format_ignores_non_edit_tool() -> None:
    out = auto_format.handle("post_tool_use", {"tool_name": "bash"})
    assert out is None


def test_auto_lint_ignores_non_edit_tool() -> None:
    out = auto_lint.handle("post_tool_use", {"tool_name": "bash"})
    assert out is None


def test_intent_classifier_build() -> None:
    out = intent_classifier.handle("prompt_submit", {"prompt": "implement a new feature"})
    assert out is not None
    assert "intent=build" in out.messages


def test_intent_classifier_debug() -> None:
    assert intent_classifier.classify("there's a crash in foo.py") == "debug"


def test_intent_classifier_unknown_for_empty() -> None:
    assert intent_classifier.handle("prompt_submit", {"prompt": ""}) is None


def test_context_recovery_warns_when_no_tool_calls() -> None:
    out = context_recovery.handle("stop", {"tool_call_count": 0})
    assert out is not None
    assert any("context_recovery" in m for m in out.messages)


def test_context_recovery_silent_when_tools_used() -> None:
    out = context_recovery.handle("stop", {"tool_call_count": 3})
    assert out is None


def test_auto_commit_offer_threshold() -> None:
    auto_commit_offer.reset()
    last = None
    for _ in range(5):
        last = auto_commit_offer.handle(
            "post_tool_use", {"tool_name": "edit_file", "auto_commit": False}
        )
    assert last is not None
    assert any("auto_commit_offer" in m for m in last.messages)
    auto_commit_offer.reset()


def test_auto_commit_offer_silent_when_auto_commit_on() -> None:
    auto_commit_offer.reset()
    out = auto_commit_offer.handle(
        "post_tool_use", {"tool_name": "edit_file", "auto_commit": True}
    )
    assert out is None


def test_fire_python_invokes_subscribers() -> None:
    runner = HookRunner()
    seen: list[str] = []

    def cb(event: str, ctx: dict) -> None:
        seen.append(event)

    runner.subscribe("prompt_submit", cb)
    runner.fire_python("prompt_submit", {})
    assert seen == ["prompt_submit"]


def test_fire_python_swallows_subscriber_exceptions() -> None:
    runner = HookRunner()

    def boom(event: str, ctx: dict) -> None:
        raise RuntimeError("nope")

    runner.subscribe("prompt_submit", boom)
    outcome = runner.fire_python("prompt_submit", {})
    assert any("builtin hook error" in m for m in outcome.messages)
