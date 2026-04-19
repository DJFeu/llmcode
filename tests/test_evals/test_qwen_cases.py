"""Tests for the Qwen eval case catalogue (C2c).

The test strategy is "fake runner drives the declared assertions":
for each case, feed a runner that mimics a well-behaved Qwen agent
through :func:`run_case` and confirm the declared expectations pass.
Then feed a runner that deliberately omits a required tool and
confirm the case fails. This keeps the assertions honest without
needing a real Qwen endpoint.
"""
from __future__ import annotations

from llm_code.evals import EvalPolicy, EvalRun, run_case
from llm_code.evals.cases.qwen import (
    CASES,
    QWEN_CHINESE_TOOL_USE,
    QWEN_CODER_ROUND_TRIP,
    QWEN_NO_THINK_LEAK,
    QWEN_XML_GLOB_THEN_READ,
    QWEN_XML_READ_FILE,
)


def _fake(text: str, tools: tuple[str, ...] = ()):
    def runner(prompt: str, timeout: float):  # noqa: ARG001
        # One rev per case so case_id stays correct across calls.
        return EvalRun(
            case_id="_",
            final_text=text,
            tool_calls=tools,
            duration_seconds=0.01,
        )

    return runner


class TestCatalogueShape:
    def test_all_cases_have_unique_ids(self) -> None:
        ids = [c.id for c in CASES]
        assert len(ids) == len(set(ids))

    def test_all_cases_tagged_qwen(self) -> None:
        for case in CASES:
            assert "qwen" in case.tags, f"missing 'qwen' tag on {case.id}"

    def test_catalogue_size(self) -> None:
        # Sprint 2 target: 3-5 Qwen cases.
        assert 3 <= len(CASES) <= 6


class TestQwenXmlReadFile:
    def test_passes_when_read_file_fires(self) -> None:
        runner = _fake(
            "Here is README.md's first line: # llmcode",
            tools=("read_file",),
        )
        result = run_case(QWEN_XML_READ_FILE, runner, times=2)
        assert result.passed is True

    def test_fails_when_read_file_missing(self) -> None:
        runner = _fake("# llmcode (guessed)", tools=())
        result = run_case(QWEN_XML_READ_FILE, runner, times=2)
        assert result.passed is False


class TestQwenXmlGlobThenRead:
    def test_passes_with_glob_and_read(self) -> None:
        runner = _fake(
            "Found llm_code/runtime/model_profile.py — defines ModelProfile.",
            tools=("glob", "read_file"),
        )
        result = run_case(QWEN_XML_GLOB_THEN_READ, runner, times=4)
        assert result.passed is True

    def test_fails_when_glob_missing(self) -> None:
        runner = _fake(
            "llm_code/runtime/model_profile.py defines profiles.",
            tools=("read_file",),
        )
        result = run_case(QWEN_XML_GLOB_THEN_READ, runner, times=4)
        # Requires BOTH tools to fire; glob absent → fail
        assert result.passed is False


class TestQwenChineseToolUse:
    def test_passes_when_glob_fires_and_md_mentioned(self) -> None:
        runner = _fake(
            "找到以下 .md 檔案：README.md、CHANGELOG.md",
            tools=("glob",),
        )
        result = run_case(QWEN_CHINESE_TOOL_USE, runner, times=4)
        assert result.passed is True

    def test_fails_when_no_md_mention(self) -> None:
        runner = _fake("這裡沒有 markdown 檔案。", tools=("glob",))
        result = run_case(QWEN_CHINESE_TOOL_USE, runner, times=4)
        assert result.passed is False


class TestQwenNoThinkLeak:
    def test_clean_answer_passes(self) -> None:
        runner = _fake("2 + 2 is 4.")
        result = run_case(QWEN_NO_THINK_LEAK, runner, times=2)
        assert result.passed is True

    def test_thinking_leak_fails(self) -> None:
        runner = _fake("<think>hmm... 2+2</think> The answer is 4.")
        result = run_case(QWEN_NO_THINK_LEAK, runner, times=2)
        # Declarative check (contains "4") passes but judge_fn rejects
        assert result.passed is False


class TestQwenCoderRoundTrip:
    def test_full_round_trip_passes(self) -> None:
        runner = _fake(
            "Wrote tmp_eval_hello.py. Read back: def greet(name): ...",
            tools=("edit_file", "read_file"),
        )
        result = run_case(QWEN_CODER_ROUND_TRIP, runner, times=4)
        assert result.passed is True

    def test_missing_read_back_fails(self) -> None:
        runner = _fake(
            "Wrote tmp_eval_hello.py with greet function.",
            tools=("edit_file",),
        )
        result = run_case(QWEN_CODER_ROUND_TRIP, runner, times=4)
        assert result.passed is False


class TestAlwaysVsUsuallyPolicies:
    def test_always_policy_case_exists(self) -> None:
        always = [c for c in CASES if c.policy is EvalPolicy.ALWAYS_PASSES]
        assert len(always) >= 1

    def test_usually_policy_case_exists(self) -> None:
        usually = [c for c in CASES if c.policy is EvalPolicy.USUALLY_PASSES]
        assert len(usually) >= 1
