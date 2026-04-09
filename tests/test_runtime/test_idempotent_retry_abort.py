"""Regression guard: the 'Aborting turn: idempotent retry loop'
detector must ACTUALLY abort the turn, not just continue the
inner dispatch loop.

Field report 2026-04-09: user's query hit this bug for ~91s,
the 'Aborting turn' log fired 3 times but the turn kept running
because the code did ``continue`` instead of breaking the outer
iteration loop. Input token count bloated to 45,732 as the model
re-emitted the same failing call across iterations.

These are source-level pins (the full turn loop needs 15+
collaborators to instantiate). A runtime-level integration test
would be ideal but requires the shared ConversationRuntime
fixture + a scripted provider that always returns the same
tool_call.
"""
from __future__ import annotations

import inspect

from llm_code.runtime.conversation import ConversationRuntime


def _get_run_turn_body_source() -> str:
    return inspect.getsource(ConversationRuntime._run_turn_body)


def test_idempotent_retry_uses_break_not_continue() -> None:
    """Regression guard for the '91s stuck loop' bug. The inner
    dispatch loop's retry-detected path must exit the tool batch
    so subsequent calls in the same batch don't run AFTER the
    abort was triggered."""
    src = _get_run_turn_body_source()
    # Find the retry-detected branch
    retry_block_start = src.find("is_idempotent_retry")
    assert retry_block_start != -1
    # Search for 'break' vs 'continue' within the next ~1500 chars
    # (covers the whole retry-detected branch body)
    block_tail = src[retry_block_start : retry_block_start + 3000]
    assert "break" in block_tail, (
        "retry-detected branch must use 'break' to exit the dispatch "
        "loop — 'continue' only skips the offending call and lets the "
        "outer iteration keep running, which wastes the entire "
        "max_turn_iterations budget on the same failing call"
    )


def test_turn_loop_breaks_on_idempotent_retry_flag() -> None:
    """The outer turn iteration loop must honor the
    ``_turn_aborted_by_retry_loop`` flag. Otherwise the model
    gets another iteration, sees the 'Aborted' error block, and
    re-emits the same call on iteration N+1."""
    src = _get_run_turn_body_source()
    assert "_turn_aborted_by_retry_loop" in src
    # The flag must be checked at the end of the iteration body
    # and trigger a break of the outer for loop
    assert "if _turn_aborted_by_retry_loop" in src


def test_idempotent_retry_emits_visible_explanation() -> None:
    """The user must see WHY the turn ended. A silent break
    leaves them wondering — a StreamTextDelta with the tool name
    makes it actionable."""
    src = _get_run_turn_body_source()
    # The yield must mention the tool name so the user knows
    # which tool was looping
    assert "retry loop" in src.lower()
    # Visible emoji warning prefix so the message stands out
    assert "⚠" in src or "warning" in src.lower()


def test_retry_tracker_still_created_per_turn() -> None:
    """The tracker lifecycle is per-turn (not per-iteration and
    not per-session). Per-turn means the tracker 'forgets' between
    user inputs, so retrying with the same query after a genuine
    failure is allowed."""
    src = _get_run_turn_body_source()
    assert "RecentToolCallTracker()" in src
