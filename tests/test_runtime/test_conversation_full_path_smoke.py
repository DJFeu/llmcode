"""Smoke test: a Hermes-format <tool_call> emitted by the provider must
be parsed and dispatched by the conversation runner.

This is the test that PRs #14/#15 should have had. They 'verified' via
`llm-code -q` quick mode, which sends NO system prompt and bypasses
`_run_turn_body` (and therefore parse_tool_calls) entirely. The smoke
tests passed for the wrong reason.

We use a fake provider that yields a single fixed response containing
the Hermes truncated form. After the turn, we assert that:
1. The runtime parsed exactly one tool call
2. The tool name matches what we put in the fake response
3. The tool dispatcher was reached (checked via a side effect on a
   stub tool that records being called)
"""
from __future__ import annotations

import pytest

# This test depends on a ConversationRuntime fixture being available.
# Mark it as skipped with a clear reason if the fixture is not present;
# Task 2's tracker tests still cover the core invariant.
pytestmark = pytest.mark.skipif(
    True,  # Flip to False once a fixture exists
    reason=(
        "Requires a ConversationRuntime test fixture. See Task 3 of "
        "docs/superpowers/plans/2026-04-08-llm-code-tool-call-resilience.md "
        "for the rationale and the format the fixture should expose."
    ),
)


@pytest.mark.asyncio
async def test_hermes_truncated_tool_call_dispatched_via_runner() -> None:
    """Ground truth: the conversation runner parses and dispatches a
    Hermes-truncated <tool_call> emitted by the provider."""
    # 1. Build a fake provider that yields the production-captured
    #    Hermes-truncated bytes from PR #16.
    captured = (
        '<tool_call>web_search>'
        '{"args": {"query": "test", "max_results": 3}}'
        '</tool_call>'
    )
    # 2. Build a stub web_search tool that records being called.
    # 3. Build a ConversationRuntime with the fake provider + stub tool.
    # 4. Run one turn with user input "test query".
    # 5. Assert the stub tool was called with the parsed args.
    raise AssertionError("Implement once ConversationRuntime fixture exists")
