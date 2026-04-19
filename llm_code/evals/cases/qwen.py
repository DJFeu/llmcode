"""Qwen-family eval cases (C2c — Sprint 2).

Curated set of cases that exercise llm-code's Qwen-specific paths:

    * XML-tagged tool calling (force_xml_tools = True)
    * Chinese-language follow-up / tool use
    * Reasoning / thinking mode leakage
    * Read → edit → verify round-trip (coder variants)

These are descriptions only — no network calls. Callers hook them up
to a real Qwen runner (vLLM / Ollama / Dashscope) through
:func:`llm_code.evals.run_case` in a nightly workflow. The assertions
are intentionally lenient: each one states the minimum a correctly
behaving Qwen agent must produce, not the best possible answer.
"""
from __future__ import annotations

from llm_code.evals.types import EvalCase, EvalPolicy

# ── XML tool use ─────────────────────────────────────────────────────

QWEN_XML_READ_FILE = EvalCase(
    id="qwen_xml_read_file",
    prompt=(
        "Read the file README.md in the current directory and tell me "
        "the first non-empty line of the document."
    ),
    policy=EvalPolicy.ALWAYS_PASSES,
    expected_tools=("read_file",),
    expected_text_contains=("README",),
    timeout_seconds=45.0,
    tags=("qwen", "xml-tools", "read"),
)

QWEN_XML_GLOB_THEN_READ = EvalCase(
    id="qwen_xml_glob_then_read",
    prompt=(
        "Find any Python file whose name ends with 'profile.py' under "
        "llm_code/ and summarise what the file defines."
    ),
    policy=EvalPolicy.USUALLY_PASSES,
    expected_tools=("glob", "read_file"),
    expected_text_contains=("profile",),
    timeout_seconds=60.0,
    tags=("qwen", "xml-tools", "glob", "read"),
)

# ── Chinese follow-up ────────────────────────────────────────────────

QWEN_CHINESE_TOOL_USE = EvalCase(
    id="qwen_chinese_tool_use",
    prompt="請列出當前目錄下所有 .md 檔案，並告訴我每個檔案的第一行標題。",
    policy=EvalPolicy.USUALLY_PASSES,
    expected_tools=("glob",),
    # We deliberately don't assert a specific Chinese phrase — models
    # may answer in English if they detect the REPL locale is en.
    # Just make sure glob fired and there's a file list.
    expected_text_contains=(".md",),
    timeout_seconds=60.0,
    tags=("qwen", "chinese", "glob"),
)

# ── Reasoning / thinking leak guard ──────────────────────────────────

QWEN_NO_THINK_LEAK = EvalCase(
    id="qwen_no_think_leak",
    prompt="What is 2 + 2? Answer in one short sentence.",
    policy=EvalPolicy.ALWAYS_PASSES,
    expected_text_contains=("4",),
    # Thinking tags must not leak into the final answer.
    judge_fn=lambda run: "<think>" not in run.final_text
    and "</think>" not in run.final_text,
    timeout_seconds=20.0,
    tags=("qwen", "thinking", "guard"),
)

# ── Read → edit → verify round-trip ──────────────────────────────────

QWEN_CODER_ROUND_TRIP = EvalCase(
    id="qwen_coder_round_trip",
    prompt=(
        "Create a new file tmp_eval_hello.py in the current directory "
        "whose only content is a function `def greet(name): return "
        "f'hello, {name}'`. Then read the file back and confirm."
    ),
    policy=EvalPolicy.USUALLY_PASSES,
    expected_tools=("edit_file", "read_file"),
    expected_text_contains=("greet",),
    timeout_seconds=90.0,
    tags=("qwen", "coder", "edit", "read"),
)

# ── Catalogue ────────────────────────────────────────────────────────

CASES: tuple[EvalCase, ...] = (
    QWEN_XML_READ_FILE,
    QWEN_XML_GLOB_THEN_READ,
    QWEN_CHINESE_TOOL_USE,
    QWEN_NO_THINK_LEAK,
    QWEN_CODER_ROUND_TRIP,
)
