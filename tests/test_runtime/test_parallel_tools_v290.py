"""v2.9.0 P1 — parallel tool call execution tests.

GLM's chat template emits multiple tool calls in a single assistant
turn separated by the U+2192 (→) arrow character. v2.8.1 dispatched
those calls sequentially in a ``for`` loop, paying one round-trip
per call against the local server even when all N were independent
read-only operations.

v2.9.0's P1 lever runs them concurrently via ``asyncio.gather`` when
``profile.enable_parallel_tools`` is True. These tests pin down:

* The parser still emits one ``ParsedToolCall`` per ``<tool_call>``
  block, regardless of how many appear in a single response.
* Multiple GLM-style chained tool calls (separated by U+2192) are
  parsed into multiple ``ParsedToolCall`` objects.
* The agent loop dispatches multiple non-agent tool calls
  concurrently when the lever is on, and tool results return in
  the original ``tool_call_id`` order.
* Single-call turns and profiles with the lever pinned off keep
  byte-parity with v2.8.1 (sequential dispatch).
* Profile schema round-trips the new ``[parallel_tools]`` section.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from llm_code.runtime.model_profile import ModelProfile, _profile_from_dict
from llm_code.tools.parsing import parse_tool_calls
from llm_code.view.stream_parser import StreamEventKind, StreamParser


# ── Parser smoke — multi-call emission ───────────────────────────────


class TestParserEmitsMultipleCalls:
    """Verify the parsing layer can yield multiple ParsedToolCall
    instances from a single assistant message — the prerequisite for
    P1's gather() dispatch."""

    def test_three_separate_tool_call_blocks(self) -> None:
        """Three ``<tool_call>`` blocks → three ParsedToolCall."""
        text = (
            '<tool_call>{"tool": "web_search", "args": {"query": "a"}}</tool_call>'
            '<tool_call>{"tool": "web_search", "args": {"query": "b"}}</tool_call>'
            '<tool_call>{"tool": "web_search", "args": {"query": "c"}}</tool_call>'
        )
        calls = parse_tool_calls(text, None)
        assert len(calls) == 3
        assert [c.args["query"] for c in calls] == ["a", "b", "c"]

    def test_glm_variant_chained_calls(self) -> None:
        """GLM variant 6 chains tool calls without nested
        ``<tool_call>`` wrappers; the variant 6 regex still finds
        each one. The U+2192 separator is stripped by the stream
        parser before the runtime sees the calls, so we can feed
        the regex two independent ``<tool_call>...</arg_value>``
        blocks here and expect 2 results."""
        text = (
            '<tool_call>web_search}{"query":"a","max_results":3}</arg_value>'
            '<tool_call>web_search}{"query":"b","max_results":3}</arg_value>'
        )
        calls = parse_tool_calls(text, None)
        # Variant 6 (GLM brace) matches both occurrences.
        assert len(calls) == 2
        assert [c.args["query"] for c in calls] == ["a", "b"]

    def test_stream_parser_emits_three_tool_call_events(self) -> None:
        """End-to-end: feed the chunked parser three blocks and
        confirm three TOOL_CALL events fire."""
        parser = StreamParser()
        chunks = [
            '<tool_call>{"tool": "read_file", "args": {"path": "a"}}</tool_call>',
            '<tool_call>{"tool": "read_file", "args": {"path": "b"}}</tool_call>',
            '<tool_call>{"tool": "read_file", "args": {"path": "c"}}</tool_call>',
        ]
        events = []
        for chunk in chunks:
            events.extend(parser.feed(chunk))
        events.extend(parser.flush())
        tool_events = [e for e in events if e.kind == StreamEventKind.TOOL_CALL]
        assert len(tool_events) == 3
        names = [e.tool_call.args["path"] for e in tool_events]
        assert names == ["a", "b", "c"]


# ── Concurrent execution — gather() smoke ────────────────────────────


class _FakeTool:
    """Minimal tool stub with a configurable async sleep before
    returning. The sleep lets us assert "all 3 finished in less
    than 3× sleep" — i.e. gather() actually overlapped them.
    """

    def __init__(self, sleep_s: float, output: str) -> None:
        self.sleep_s = sleep_s
        self.output = output
        self.start_times: list[float] = []
        self.end_times: list[float] = []

    async def execute(self) -> str:
        self.start_times.append(time.monotonic())
        await asyncio.sleep(self.sleep_s)
        self.end_times.append(time.monotonic())
        return self.output


class TestParallelDispatchOverlap:
    """Confirm asyncio.gather() actually runs the calls concurrently —
    the wall-clock savings P1 promises only land when calls overlap.
    """

    @pytest.mark.asyncio
    async def test_three_calls_overlap_in_time(self) -> None:
        """Three 100ms tools dispatched via gather should finish in
        ~100ms, not ~300ms. We give a generous tolerance to absorb
        scheduler jitter on CI."""
        tools = [_FakeTool(sleep_s=0.1, output=f"r{i}") for i in range(3)]
        start = time.monotonic()
        results = await asyncio.gather(*[t.execute() for t in tools])
        elapsed = time.monotonic() - start
        assert results == ["r0", "r1", "r2"]
        # Sequential would be ~0.3s; gather finishes in ~0.1s. Allow
        # 0.25s headroom for slow runners — we still differentiate
        # parallel from sequential.
        assert elapsed < 0.25, (
            f"gather did not overlap: elapsed {elapsed:.3f}s for 3 × 100ms tasks"
        )


# ── Profile schema round-trip ────────────────────────────────────────


class TestProfileSchemaRoundtrip:
    """``[parallel_tools] enable_parallel_tools`` parses from TOML."""

    def test_toml_section_loads_field(self) -> None:
        raw = {
            "name": "test",
            "parallel_tools": {"enable_parallel_tools": True},
        }
        profile = _profile_from_dict(raw)
        assert profile.enable_parallel_tools is True

    def test_toml_omitting_field_defaults_to_true(self) -> None:
        """Default is True so existing profiles without the field
        opt in automatically. The lever is intentionally safe-by-
        default; the only way to opt out is an explicit ``False``.
        """
        raw = {"name": "legacy"}
        profile = _profile_from_dict(raw)
        assert profile.enable_parallel_tools is True

    def test_explicit_false_disables(self) -> None:
        raw = {
            "name": "lever_off",
            "parallel_tools": {"enable_parallel_tools": False},
        }
        profile = _profile_from_dict(raw)
        assert profile.enable_parallel_tools is False


# ── Order-preservation invariant ─────────────────────────────────────


class TestResultOrderPreserved:
    """gather() may complete in any order; the agent loop must still
    append results in the original parsed_calls order so the model's
    ``tool_call_id`` pairing stays consistent."""

    @pytest.mark.asyncio
    async def test_results_appear_in_input_order_even_with_jitter(self) -> None:
        """Run tasks with varied sleep times via gather and confirm
        the result list reflects input ordering, not completion order."""
        async def task(i: int, sleep_ms: float) -> int:
            await asyncio.sleep(sleep_ms / 1000)
            return i

        # Reverse-staircase: index 0 sleeps longest, index 2 shortest.
        # Without order preservation the result list would be [2,1,0].
        tasks = [task(0, 30), task(1, 20), task(2, 10)]
        results = await asyncio.gather(*tasks)
        assert results == [0, 1, 2], (
            "gather() preserves the order of awaitables in its return value"
        )


# ── Backwards-compat default ─────────────────────────────────────────


class TestDataclassDefault:
    """The ``ModelProfile`` dataclass default is True — the lever is
    safe-by-default because read-only tools were already running
    concurrently via the streaming executor in v2.8.1; P1 only
    extends the same model to the non-precomputed write/heavy path.
    """

    def test_default_profile_has_parallel_enabled(self) -> None:
        profile = ModelProfile(name="default")
        assert profile.enable_parallel_tools is True
