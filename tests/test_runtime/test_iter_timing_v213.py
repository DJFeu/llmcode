"""v2.13.0 Lever 2 — per-iter diagnostic timing log tests.

Each iteration of the agent loop emits a structured ``iter_timing:``
DEBUG line capturing prefill_in / out / thinking_chars / tool_calls
plus per-phase wall-clock (``provider_s``, ``tool_s``, ``bookkeeping_s``,
``iter_total_s``) plus the iter's ``stop_reason``. The turn-end emits
a single ``turn_timing:`` summary aggregating provider_total_s and
tool_total_s across all iterations.

These tests pin down:

* Each iteration produces exactly one ``iter_timing:`` line.
* Each turn produces exactly one ``turn_timing:`` summary line.
* All parsed timing values are non-negative floats (``time.monotonic()``
  is monotonic so any negative value would indicate a logic bug in
  the marker capture).

Timing values themselves are NOT pinned — they're flaky under CI load.
The tests parse for substring presence and non-negative float shape.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import AsyncIterator

import pytest

from llm_code.api.types import (
    MessageRequest,
    StreamEvent,
    StreamMessageStop,
    StreamTextDelta,
    TokenUsage,
)
from llm_code.runtime.context import ProjectContext
from llm_code.runtime.conversation import ConversationRuntime
from llm_code.runtime.permissions import PermissionMode, PermissionPolicy
from llm_code.runtime.prompt import SystemPromptBuilder
from llm_code.runtime.session import Session
from llm_code.tools.registry import ToolRegistry


# ── Helpers (mirror tests/test_runtime/test_conversation.py shapes) ──


def _make_context(tmp_path: Path) -> ProjectContext:
    return ProjectContext(
        cwd=tmp_path,
        is_git_repo=False,
        git_status="",
        instructions="",
    )


async def _text_stream(text: str = "ok", in_tok: int = 10, out_tok: int = 5) -> AsyncIterator[StreamEvent]:
    yield StreamTextDelta(text=text)
    yield StreamMessageStop(
        usage=TokenUsage(in_tok, out_tok), stop_reason="end_turn",
    )


class MockProvider:
    """Mock LLM provider — returns one or more pre-set streams."""

    def __init__(self, response_streams: list) -> None:
        self._streams = iter(response_streams)
        self._call_count = 0

    async def stream_message(self, request: MessageRequest) -> AsyncIterator[StreamEvent]:
        self._call_count += 1
        return next(self._streams)

    def supports_native_tools(self) -> bool:
        return True

    def supports_images(self) -> bool:
        return False

    def supports_reasoning(self) -> bool:
        return False


def _make_runtime(
    tmp_path: Path,
    provider: MockProvider,
) -> ConversationRuntime:
    registry = ToolRegistry()
    permission_policy = PermissionPolicy(mode=PermissionMode.AUTO_ACCEPT)

    class _NoOpHooks:
        async def pre_tool_use(self, tool_name: str, args: dict) -> dict:
            return args

        async def post_tool_use(self, tool_name: str, args: dict, result) -> None:
            pass

    class _Config:
        max_turn_iterations = 5
        max_tokens = 4096
        temperature = 0.7
        native_tools = True
        compact_after_tokens = 80000

    session = Session.create(tmp_path)
    context = _make_context(tmp_path)
    return ConversationRuntime(
        provider=provider,
        tool_registry=registry,
        permission_policy=permission_policy,
        hook_runner=_NoOpHooks(),
        prompt_builder=SystemPromptBuilder(),
        config=_Config(),
        session=session,
        context=context,
    )


# ── Tests ────────────────────────────────────────────────────────────


class TestIterTimingLogs:
    """Per-iter structured DEBUG line emitted on every iteration."""

    @pytest.mark.asyncio
    async def test_single_iter_emits_one_iter_timing_line(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A turn that ends in iter 0 (text-only response, no tools)
        produces exactly one ``iter_timing:`` line.
        """
        caplog.set_level(logging.DEBUG, logger="llm_code.runtime.conversation")
        provider = MockProvider([_text_stream()])
        runtime = _make_runtime(tmp_path, provider)

        async for _ in runtime.run_turn("hi"):
            pass

        iter_lines = [
            r.getMessage() for r in caplog.records
            if r.getMessage().startswith("iter_timing:")
        ]
        assert len(iter_lines) == 1, (
            f"expected exactly 1 iter_timing line; got {len(iter_lines)}\n"
            f"lines: {iter_lines}"
        )
        # Sanity-check the labelled keys are in the line.
        assert "iter=0" in iter_lines[0]
        assert "provider_s=" in iter_lines[0]
        assert "tool_s=" in iter_lines[0]
        assert "iter_total_s=" in iter_lines[0]
        assert "stop_reason=" in iter_lines[0]


class TestTurnTimingSummary:
    """Single ``turn_timing:`` summary line per turn."""

    @pytest.mark.asyncio
    async def test_turn_emits_exactly_one_summary(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture,
    ) -> None:
        caplog.set_level(logging.DEBUG, logger="llm_code.runtime.conversation")
        provider = MockProvider([_text_stream()])
        runtime = _make_runtime(tmp_path, provider)

        async for _ in runtime.run_turn("hi"):
            pass

        summary_lines = [
            r.getMessage() for r in caplog.records
            if r.getMessage().startswith("turn_timing:")
        ]
        assert len(summary_lines) == 1, (
            f"expected exactly 1 turn_timing line; got {len(summary_lines)}\n"
            f"lines: {summary_lines}"
        )
        assert "iters=" in summary_lines[0]
        assert "total_s=" in summary_lines[0]
        assert "provider_total_s=" in summary_lines[0]
        assert "tool_total_s=" in summary_lines[0]


class TestNonNegativeTimings:
    """All parsed timing fields are non-negative floats."""

    @pytest.mark.asyncio
    async def test_iter_timing_values_are_non_negative(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture,
    ) -> None:
        """``time.monotonic()`` is monotonic — any negative timing
        value indicates a logic bug in the marker capture.
        """
        caplog.set_level(logging.DEBUG, logger="llm_code.runtime.conversation")
        provider = MockProvider([_text_stream()])
        runtime = _make_runtime(tmp_path, provider)

        async for _ in runtime.run_turn("hi"):
            pass

        iter_lines = [
            r.getMessage() for r in caplog.records
            if r.getMessage().startswith("iter_timing:")
        ]
        assert iter_lines, "no iter_timing lines emitted"

        # Pull every <key>=<float> pair and assert >= 0.0.
        timing_keys = (
            "provider_s", "tool_s", "bookkeeping_s", "iter_total_s",
        )
        for line in iter_lines:
            for key in timing_keys:
                m = re.search(rf"{key}=([0-9]+(?:\.[0-9]+)?)", line)
                assert m, f"key {key!r} missing from line: {line}"
                value = float(m.group(1))
                assert value >= 0.0, (
                    f"{key} = {value} on line: {line}"
                )

    @pytest.mark.asyncio
    async def test_turn_timing_values_are_non_negative(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture,
    ) -> None:
        caplog.set_level(logging.DEBUG, logger="llm_code.runtime.conversation")
        provider = MockProvider([_text_stream()])
        runtime = _make_runtime(tmp_path, provider)

        async for _ in runtime.run_turn("hi"):
            pass

        summary_lines = [
            r.getMessage() for r in caplog.records
            if r.getMessage().startswith("turn_timing:")
        ]
        assert summary_lines

        timing_keys = ("total_s", "provider_total_s", "tool_total_s")
        for line in summary_lines:
            for key in timing_keys:
                m = re.search(rf"{key}=([0-9]+(?:\.[0-9]+)?)", line)
                assert m, f"key {key!r} missing from line: {line}"
                value = float(m.group(1))
                assert value >= 0.0, f"{key} = {value} on line: {line}"
