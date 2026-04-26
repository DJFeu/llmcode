"""v15 M3 parity gate — pre/post-refactor byte equivalence.

Loads ``tests/fixtures/conversion_corpus.json`` (captured with the
v2.4.0 codebase by ``scripts/capture_conversion_corpus.py``) and
asserts that every scenario's converted ``dict[]`` is byte-identical
when produced by the new shared ``llm_code.api.conversion.serialize_messages``
function. Any drift fails CI.

Two parametrized test sets:

* ``test_anthropic_byte_parity`` — runs ``serialize_messages`` with
  ``target_shape='anthropic'`` and compares to ``expected_anthropic``.
* ``test_openai_byte_parity`` — runs with ``target_shape='openai'``
  and compares to ``expected_openai``.

Each scenario is its own pytest case so failures pinpoint the exact
edge case that drifted.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from llm_code.api.conversion import (
    ConversionContext,
    ReasoningReplayMode,
    serialize_messages,
)
from llm_code.api.types import (
    ImageBlock,
    Message,
    ServerToolResultBlock,
    ServerToolUseBlock,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
)

_CORPUS_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "fixtures" / "conversion_corpus.json"
)


def _dict_to_block(data: dict[str, Any]) -> Any:
    t = data["_type"]
    if t == "text":
        return TextBlock(text=data["text"])
    if t == "thinking":
        return ThinkingBlock(content=data["content"], signature=data["signature"])
    if t == "tool_use":
        return ToolUseBlock(id=data["id"], name=data["name"], input=data["input"])
    if t == "tool_result":
        return ToolResultBlock(
            tool_use_id=data["tool_use_id"],
            content=data["content"],
            is_error=data["is_error"],
        )
    if t == "image":
        return ImageBlock(media_type=data["media_type"], data=data["data"])
    if t == "server_tool_use":
        return ServerToolUseBlock(
            id=data["id"], name=data["name"], input=data["input"],
            signature=data["signature"],
        )
    if t == "server_tool_result":
        return ServerToolResultBlock(
            tool_use_id=data["tool_use_id"],
            content=data["content"],
            signature=data["signature"],
        )
    raise ValueError(f"unhandled tagged block: {t}")


def _dict_to_msg(data: dict[str, Any]) -> Message:
    return Message(
        role=data["role"],
        content=tuple(_dict_to_block(b) for b in data["content"]),
    )


def _load_corpus() -> list[dict[str, Any]]:
    if not _CORPUS_PATH.exists():
        pytest.skip(
            f"corpus not captured at {_CORPUS_PATH} — run "
            f"scripts/capture_conversion_corpus.py first"
        )
    return json.loads(_CORPUS_PATH.read_text())


_CORPUS: list[dict[str, Any]] = _load_corpus()
_SCENARIO_IDS = [s["name"] for s in _CORPUS]


@pytest.mark.parametrize(
    "scenario", _CORPUS, ids=_SCENARIO_IDS,
)
def test_anthropic_byte_parity(scenario: dict[str, Any]) -> None:
    """Anthropic wire-shape conversion matches the captured corpus."""
    messages = tuple(_dict_to_msg(m) for m in scenario["input_messages"])
    ctx = ConversionContext(
        target_shape="anthropic",
        reasoning_replay=ReasoningReplayMode.NATIVE_THINKING,
        strip_prior_reasoning=False,
    )
    actual = serialize_messages(messages, ctx)
    expected = scenario["expected_anthropic"]
    assert actual == expected, (
        f"Anthropic conversion drift on scenario {scenario['name']!r}.\n"
        f"  expected = {json.dumps(expected, indent=2)[:600]}\n"
        f"  actual   = {json.dumps(actual, indent=2)[:600]}"
    )


@pytest.mark.parametrize(
    "scenario", _CORPUS, ids=_SCENARIO_IDS,
)
def test_openai_byte_parity(scenario: dict[str, Any]) -> None:
    """OpenAI-compat wire-shape conversion matches the captured corpus."""
    messages = tuple(_dict_to_msg(m) for m in scenario["input_messages"])
    ctx = ConversionContext(
        target_shape="openai",
        reasoning_replay=ReasoningReplayMode.DISABLED,
        strip_prior_reasoning=scenario.get("strip_prior_reasoning", False),
    )
    actual = serialize_messages(
        messages, ctx, system=scenario.get("input_system"),
    )
    expected = scenario["expected_openai"]
    assert actual == expected, (
        f"OpenAI conversion drift on scenario {scenario['name']!r}.\n"
        f"  expected = {json.dumps(expected, indent=2)[:600]}\n"
        f"  actual   = {json.dumps(actual, indent=2)[:600]}"
    )
