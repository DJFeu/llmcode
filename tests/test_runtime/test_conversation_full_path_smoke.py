"""Smoke test: a Hermes-format <tool_call> emitted by the provider must
be parsed and dispatched by the conversation runner.

This is the test that PRs #14/#15 should have had. They "verified" via
``llm-code -q`` quick mode, which (before 2026-04-08) sent NO system
prompt and bypassed ``_run_turn_body`` — and therefore the tool-call
parser — entirely. The smoke tests passed for the wrong reason.

We use a canned-response provider that yields a single fixed response
containing the Hermes truncated form. After the turn we assert:

1. The runtime reached the tool dispatcher (side-effect on a stub tool)
2. The dispatcher was called with the parsed args
3. A StreamToolExecStart event was emitted so renderers see the dispatch
"""
from __future__ import annotations

import pytest

from llm_code.api.types import StreamToolExecStart

from tests.fixtures.runtime import make_conv_runtime


@pytest.mark.asyncio
async def test_hermes_truncated_tool_call_dispatched_via_runner() -> None:
    """Ground truth: the conversation runner parses and dispatches a
    Hermes-truncated <tool_call> emitted by the provider."""
    captured = (
        '<tool_call>web_search>'
        '{"args": {"query": "test", "max_results": 3}}'
        '</tool_call>'
    )
    dispatched: list[tuple[str, dict]] = []

    def _web_search(args: dict) -> dict:
        dispatched.append(("web_search", args))
        return {"output": "fake results", "is_error": False}

    runtime = make_conv_runtime(
        canned_response_text=captured,
        extra_tools={"web_search": _web_search},
    )
    events = await runtime.run_one_turn("test query")

    assert dispatched == [("web_search", {"query": "test", "max_results": 3})]
    assert any(isinstance(ev, StreamToolExecStart) for ev in events), (
        "expected a StreamToolExecStart event from the dispatched tool"
    )
