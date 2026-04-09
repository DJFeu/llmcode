"""Regression guard for the stale-local force_xml bug in
ConversationRuntime (observed in a Qwen3.5 field report: second
iteration of a tool-use turn retried native mode even though
iteration 1 had already discovered the server doesn't support it,
burning ~19s on duplicate retry storm).

The fix: read ``self._force_xml_mode`` fresh each iteration
instead of caching a local variable at turn start.

These are source-level guards because instantiating a full
ConversationRuntime for this test needs 15+ collaborators. The
contract is pinned by inspecting the method source — a future
refactor that reintroduces a stale local will break these tests.
"""
from __future__ import annotations

import inspect

from llm_code.runtime.conversation import ConversationRuntime


def _get_run_turn_body_source() -> str:
    return inspect.getsource(ConversationRuntime._run_turn_body)


def test_run_turn_body_does_not_cache_force_xml_as_local() -> None:
    """The obsolete pattern ``force_xml = getattr(self,
    "_force_xml_mode", False)`` must not exist in the method body.
    If this assertion fires, someone reintroduced the local shadow
    and iteration 2 within a turn will again burn time retrying
    native mode."""
    src = _get_run_turn_body_source()
    # Must not assign force_xml as a plain local at turn setup
    assert "force_xml = getattr(self" not in src


def test_use_native_reads_force_xml_mode_as_attribute() -> None:
    """The ``use_native`` computation must read
    ``self._force_xml_mode`` so that iteration 2's retry sees the
    fallback flag set by iteration 1."""
    src = _get_run_turn_body_source()
    assert "not self._force_xml_mode" in src
    # And the old stale-local read must be gone
    assert "and not force_xml" not in src


def test_force_xml_mode_initialized_in_run_turn_body() -> None:
    """The attribute must exist on self before the iteration loop
    starts, so the first turn has a safe default."""
    src = _get_run_turn_body_source()
    assert "self._force_xml_mode = False" in src or "hasattr(self, \"_force_xml_mode\")" in src


def test_xml_fallback_branch_still_sets_the_attribute() -> None:
    """The except branch that detects 'tool-call-parser' errors
    must still flip self._force_xml_mode = True so subsequent
    iterations stay in XML mode. Regression guard for the original
    wave2-3 fallback wiring."""
    src = _get_run_turn_body_source()
    assert "self._force_xml_mode = True" in src
    assert "tool-call-parser" in src
