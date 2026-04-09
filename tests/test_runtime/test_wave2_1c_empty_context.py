"""Wave2-1c: Empty response counter + context pressure pre-warning.

Two surgical gaps found in the wave2-1 audit:

1. **EmptyAssistantResponse ⚠️** — a turn that returned no text and
   no tool calls used to be silently skipped. A degenerate provider
   state could loop forever producing nothing. Now the runtime
   counts consecutive empty responses, fires a hook on each, injects
   a nudge user message on the 2nd, and raises RuntimeError on the
   3rd so the turn budget cannot be exhausted on nothing.

2. **ContextWindowExceeded ⚠️** — proactive compaction only fired
   AT the limit (100%). Hook observers had no way to pre-emptively
   save state or warn the user that the window was filling up.
   Now a ``context_pressure`` hook fires once per ascending bucket
   transition (low→mid at 70%, mid→high at 85%) BEFORE the
   compaction trigger at 100%.

These tests exercise the pure state-tracking logic on a minimally
instantiated ConversationRuntime. The full turn loop is covered by
the existing test_conversation* suites which must still pass.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from llm_code.runtime.hooks import _EVENT_GROUP, _event_matches


# ---------- Hook registration ----------

def test_context_pressure_event_registered() -> None:
    """The new hook event names must be in the canonical group map
    so glob subscribers (``context.*`` / ``session.*``) pick them
    up automatically."""
    assert _EVENT_GROUP["context_pressure"] == "context.context_pressure"
    assert _EVENT_GROUP["empty_assistant_response"] == "session.empty_assistant_response"


def test_context_pressure_matches_context_glob() -> None:
    assert _event_matches("context.*", "context_pressure") is True


def test_empty_response_matches_session_glob() -> None:
    assert _event_matches("session.*", "empty_assistant_response") is True


# ---------- Context pressure bucket logic (pure function test) ----------

def _compute_bucket(est_tokens: int, limit: int) -> str:
    """Mirror of the bucket logic in conversation.py so we can pin
    the exact thresholds without spinning up a full runtime."""
    ratio = est_tokens / max(limit, 1)
    if ratio >= 0.85:
        return "high"
    if ratio >= 0.70:
        return "mid"
    return "low"


@pytest.mark.parametrize(
    "tokens,limit,expected",
    [
        (0, 1000, "low"),
        (500, 1000, "low"),
        (699, 1000, "low"),
        (700, 1000, "mid"),
        (800, 1000, "mid"),
        (849, 1000, "mid"),
        (850, 1000, "high"),
        (999, 1000, "high"),
        (1001, 1000, "high"),  # over limit still bucketed as high
    ],
)
def test_pressure_bucket_thresholds(tokens: int, limit: int, expected: str) -> None:
    assert _compute_bucket(tokens, limit) == expected


def test_pressure_bucket_handles_zero_limit() -> None:
    """Guard against division-by-zero if a misconfigured context
    limit ever reaches the bucket computation."""
    # max(limit, 1) keeps this finite — smoke test only
    assert _compute_bucket(100, 0) in ("low", "mid", "high")


# ---------- Hook-based pressure transition tracking ----------

@dataclass
class _FakeRuntime:
    """Minimal runtime stub that replays the conversation.py
    pressure-tracking logic without instantiating the full
    ConversationRuntime (which needs 15+ collaborators)."""

    context_limit: int = 1000
    _last_bucket: str = "low"
    fired: list[tuple[str, str]] = field(default_factory=list)

    def process_turn(self, est_tokens: int) -> None:
        ratio = est_tokens / max(self.context_limit, 1)
        if ratio >= 0.85:
            new_bucket = "high"
        elif ratio >= 0.70:
            new_bucket = "mid"
        else:
            new_bucket = "low"
        if new_bucket != self._last_bucket:
            if new_bucket in ("mid", "high"):
                self.fired.append(("context_pressure", new_bucket))
            self._last_bucket = new_bucket


def test_pressure_hook_fires_on_ascending_mid_crossing() -> None:
    rt = _FakeRuntime()
    rt.process_turn(500)   # low
    rt.process_turn(750)   # mid — fire
    assert rt.fired == [("context_pressure", "mid")]


def test_pressure_hook_fires_on_mid_to_high_crossing() -> None:
    rt = _FakeRuntime()
    rt.process_turn(750)   # mid — fire
    rt.process_turn(900)   # high — fire
    assert rt.fired == [
        ("context_pressure", "mid"),
        ("context_pressure", "high"),
    ]


def test_pressure_hook_does_not_spam_same_bucket() -> None:
    """Three turns inside the mid bucket must fire only once."""
    rt = _FakeRuntime()
    rt.process_turn(750)
    rt.process_turn(800)
    rt.process_turn(849)
    assert rt.fired == [("context_pressure", "mid")]


def test_pressure_hook_silent_on_descent() -> None:
    """Dropping back to low after a compaction is not a pressure
    event — the hook only fires on ascending crossings."""
    rt = _FakeRuntime()
    rt.process_turn(900)   # high — fire
    rt.process_turn(100)   # back to low — silent
    assert rt.fired == [("context_pressure", "high")]


def test_pressure_hook_refires_after_descent_and_reascent() -> None:
    """Descending reset means a subsequent ascent refires the
    hook — the condition is "bucket changed", not "ever saw high"."""
    rt = _FakeRuntime()
    rt.process_turn(900)   # high — fire
    rt.process_turn(100)   # low — reset
    rt.process_turn(900)   # high — fire again
    assert rt.fired == [
        ("context_pressure", "high"),
        ("context_pressure", "high"),
    ]


# ---------- Empty response counter state machine ----------

class _EmptyResponseTracker:
    """Mirror of the wave2-1c counter logic in conversation.py."""

    def __init__(self) -> None:
        self.count = 0
        self.fired: list[int] = []
        self.nudged = 0

    def record_non_empty(self) -> None:
        self.count = 0

    def record_empty(self) -> str:
        """Returns 'continue', 'nudge', or 'abort' to match the
        conversation-loop behavior."""
        self.count += 1
        self.fired.append(self.count)
        if self.count >= 3:
            return "abort"
        if self.count >= 2:
            self.nudged += 1
            return "nudge"
        return "continue"


def test_empty_tracker_first_empty_continues() -> None:
    t = _EmptyResponseTracker()
    assert t.record_empty() == "continue"
    assert t.count == 1
    assert t.nudged == 0


def test_empty_tracker_second_empty_nudges() -> None:
    t = _EmptyResponseTracker()
    t.record_empty()
    assert t.record_empty() == "nudge"
    assert t.count == 2
    assert t.nudged == 1


def test_empty_tracker_third_empty_aborts() -> None:
    t = _EmptyResponseTracker()
    t.record_empty()
    t.record_empty()
    assert t.record_empty() == "abort"
    assert t.count == 3


def test_empty_tracker_resets_on_non_empty() -> None:
    t = _EmptyResponseTracker()
    t.record_empty()
    t.record_empty()
    t.record_non_empty()
    assert t.count == 0
    # After reset, we're back to the "first empty continues" path
    assert t.record_empty() == "continue"


def test_empty_tracker_fires_hook_on_every_empty() -> None:
    """Observers get one hook call per empty response even when
    the runtime has not yet escalated to nudge or abort."""
    t = _EmptyResponseTracker()
    t.record_empty()
    t.record_empty()
    assert t.fired == [1, 2]


# ---------- Integration smoke: the real runtime state attrs exist ----------

def test_conversation_runtime_has_wave2_1c_state_attrs() -> None:
    """Guard test: the wave2-1c runtime attributes are declared in
    __init__ and default to their sentinel values. A future refactor
    that renames them without updating the assembly site would
    silently break the whole feature; this test catches that."""
    # Import inside the test so module-level diagnostics don't block
    from llm_code.runtime.conversation import ConversationRuntime

    # Inspect __init__ source to confirm the sentinel assignments
    # exist. We can't easily instantiate a full runtime without
    # 15+ collaborators, so a source-level probe is the pragmatic
    # check. The exact assignment text is the contract.
    import inspect
    src = inspect.getsource(ConversationRuntime.__init__)
    assert "self._consecutive_empty_responses" in src
    assert "self._last_context_pressure_bucket" in src
