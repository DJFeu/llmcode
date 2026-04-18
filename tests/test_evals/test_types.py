"""Tests for the eval framework types (C2a — Sprint 2).

The eval types layer is pure data. Runner and pytest integration live
in separate modules (C2b / C2c) so this commit stays reviewable.
"""
from __future__ import annotations

import pytest

from llm_code.evals import (
    EvalCase,
    EvalPolicy,
    EvalResult,
    EvalRun,
    check_run,
)


# ---------- EvalPolicy ----------


class TestEvalPolicy:
    def test_enum_values(self) -> None:
        assert EvalPolicy.ALWAYS_PASSES.value == "always_passes"
        assert EvalPolicy.USUALLY_PASSES.value == "usually_passes"

    def test_from_string_case_insensitive(self) -> None:
        assert EvalPolicy.from_string("always_passes") is EvalPolicy.ALWAYS_PASSES
        assert EvalPolicy.from_string("ALWAYS_PASSES") is EvalPolicy.ALWAYS_PASSES
        assert EvalPolicy.from_string("usually_passes") is EvalPolicy.USUALLY_PASSES

    def test_from_string_rejects_unknown(self) -> None:
        with pytest.raises(ValueError):
            EvalPolicy.from_string("sometimes_passes")


# ---------- EvalCase ----------


class TestEvalCase:
    def test_frozen(self) -> None:
        case = EvalCase(
            id="c1", prompt="hello", policy=EvalPolicy.ALWAYS_PASSES,
        )
        with pytest.raises(Exception):
            case.id = "c2"  # type: ignore[misc]

    def test_defaults(self) -> None:
        case = EvalCase(id="c1", prompt="p", policy=EvalPolicy.ALWAYS_PASSES)
        assert case.expected_tools == ()
        assert case.expected_text_contains == ()
        assert case.judge_fn is None
        assert case.timeout_seconds == 60.0
        assert case.tags == ()

    def test_full_construction(self) -> None:
        case = EvalCase(
            id="qwen_xml_tool",
            prompt="read README.md",
            policy=EvalPolicy.USUALLY_PASSES,
            expected_tools=("read_file",),
            expected_text_contains=("README",),
            timeout_seconds=30.0,
            tags=("qwen", "xml-tools"),
        )
        assert case.expected_tools == ("read_file",)
        assert "qwen" in case.tags


# ---------- EvalRun ----------


class TestEvalRun:
    def test_success_run(self) -> None:
        run = EvalRun(
            case_id="c1",
            final_text="Here is the content.",
            tool_calls=("read_file",),
            duration_seconds=2.5,
        )
        assert run.error is None
        assert run.is_success is True

    def test_error_run(self) -> None:
        run = EvalRun(
            case_id="c1",
            final_text="",
            tool_calls=(),
            duration_seconds=0.1,
            error="timeout",
        )
        assert run.is_success is False


# ---------- check_run ----------


class TestCheckRun:
    def _case(self, **kwargs) -> EvalCase:
        defaults = dict(id="c", prompt="p", policy=EvalPolicy.ALWAYS_PASSES)
        defaults.update(kwargs)
        return EvalCase(**defaults)

    def _run(self, **kwargs) -> EvalRun:
        defaults = dict(
            case_id="c", final_text="", tool_calls=(), duration_seconds=0.0,
        )
        defaults.update(kwargs)
        return EvalRun(**defaults)

    def test_passes_empty_assertions(self) -> None:
        assert check_run(self._case(), self._run(final_text="anything")) is True

    def test_fails_when_error_set(self) -> None:
        assert check_run(self._case(), self._run(error="timeout")) is False

    def test_expected_tools_must_all_fire(self) -> None:
        case = self._case(expected_tools=("read_file", "edit_file"))
        ok = self._run(tool_calls=("read_file", "edit_file"))
        missing = self._run(tool_calls=("read_file",))
        assert check_run(case, ok) is True
        assert check_run(case, missing) is False

    def test_expected_tools_extra_tools_allowed(self) -> None:
        """Extra tool calls beyond the expected set are fine."""
        case = self._case(expected_tools=("read_file",))
        run = self._run(tool_calls=("read_file", "glob", "grep"))
        assert check_run(case, run) is True

    def test_expected_text_contains_all(self) -> None:
        case = self._case(expected_text_contains=("hello", "world"))
        run = self._run(final_text="hello, world!")
        assert check_run(case, run) is True

    def test_expected_text_missing_substring(self) -> None:
        case = self._case(expected_text_contains=("hello", "unicorn"))
        run = self._run(final_text="hello, world")
        assert check_run(case, run) is False

    def test_custom_judge_fn_runs_last(self) -> None:
        """judge_fn should see a run that already satisfied the
        declarative expected_* checks; when provided, its return value
        is final."""
        calls: list[EvalRun] = []

        def judge(run: EvalRun) -> bool:
            calls.append(run)
            return "secret-token" in run.final_text

        case = self._case(
            expected_text_contains=("ok",),
            judge_fn=judge,
        )
        # Declarative check passes, judge_fn rejects → overall False
        bad = self._run(final_text="ok but no secret")
        assert check_run(case, bad) is False
        # Declarative passes, judge_fn accepts → overall True
        good = self._run(final_text="ok secret-token")
        assert check_run(case, good) is True
        assert len(calls) == 2

    def test_custom_judge_not_called_when_declarative_fails(self) -> None:
        called = []

        def judge(run: EvalRun) -> bool:
            called.append(1)
            return True

        case = self._case(
            expected_tools=("read_file",),
            judge_fn=judge,
        )
        # Expected tool didn't fire → judge should NOT run
        run = self._run(tool_calls=())
        assert check_run(case, run) is False
        assert called == []


# ---------- EvalResult aggregation ----------


class TestEvalResult:
    def test_frozen(self) -> None:
        case = EvalCase(id="c", prompt="p", policy=EvalPolicy.ALWAYS_PASSES)
        result = EvalResult(case=case, runs=(), passed=False,
                            passing_runs=0, total_runs=0)
        with pytest.raises(Exception):
            result.passed = True  # type: ignore[misc]

    def test_pass_rate(self) -> None:
        case = EvalCase(id="c", prompt="p", policy=EvalPolicy.USUALLY_PASSES)
        result = EvalResult(
            case=case, runs=(), passed=True,
            passing_runs=3, total_runs=4,
        )
        assert result.pass_rate == 0.75

    def test_pass_rate_zero_total_is_zero(self) -> None:
        case = EvalCase(id="c", prompt="p", policy=EvalPolicy.ALWAYS_PASSES)
        result = EvalResult(case=case, runs=(), passed=False,
                            passing_runs=0, total_runs=0)
        assert result.pass_rate == 0.0
