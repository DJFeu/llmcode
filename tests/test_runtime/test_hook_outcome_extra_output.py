"""HookOutcome.extra_output should accumulate across in-process subscribers."""
from __future__ import annotations

from llm_code.runtime.hooks import HookOutcome, HookRunner


def test_extra_output_default_is_empty_string() -> None:
    out = HookOutcome()
    assert out.extra_output == ""


def test_fire_python_concatenates_extra_output_from_multiple_subscribers() -> None:
    runner = HookRunner()

    def first(event: str, context: dict) -> HookOutcome:
        return HookOutcome(extra_output="\n[rule A]\nbody A")

    def second(event: str, context: dict) -> HookOutcome:
        return HookOutcome(extra_output="\n[rule B]\nbody B")

    runner.subscribe("post_tool_use", first)
    runner.subscribe("post_tool_use", second)
    outcome = runner.fire_python("post_tool_use", {})

    assert "[rule A]" in outcome.extra_output
    assert "[rule B]" in outcome.extra_output
    assert outcome.extra_output.index("[rule A]") < outcome.extra_output.index("[rule B]")


def test_fire_python_denied_subscriber_short_circuits_extra_output() -> None:
    runner = HookRunner()

    def appender(event: str, context: dict) -> HookOutcome:
        return HookOutcome(extra_output="visible")

    def denier(event: str, context: dict) -> HookOutcome:
        return HookOutcome(denied=True, messages=["blocked"])

    runner.subscribe("post_tool_use", appender)
    runner.subscribe("post_tool_use", denier)
    outcome = runner.fire_python("post_tool_use", {})

    assert outcome.denied is True
    assert outcome.extra_output == ""
