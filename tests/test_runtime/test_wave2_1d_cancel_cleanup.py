"""Wave2-1d: CancelledError cleanup on interrupted tool execution.

The ``_execute_tool_with_streaming`` method is an async generator
that runs tool code in a ThreadPoolExecutor. When a user ctrl+c or
parent-task timeout arrives, the await on the progress queue or the
future can raise ``asyncio.CancelledError``. Before wave2-1d the
exception propagated without any cleanup, leaving an orphan
ToolUseBlock in the session with no matching ToolResultBlock — the
conversation round-trip invariant broke and the next turn's payload
was malformed.

The fix wraps the progress drain + future wait in a try/except that:

1. Fires the new ``tool_cancelled`` hook so observers see the
   interruption distinctly from a normal tool_error.
2. Yields an ``is_error=True`` ToolResultBlock so the session has
   a terminal record for the interrupted call.
3. Re-raises the ``CancelledError`` so the parent task shutdown
   path keeps working.

The ThreadPoolExecutor worker thread itself cannot be interrupted —
``tool.execute_with_progress`` continues to completion in the
background — but the *session* state is now consistent regardless.

These tests exercise the exception-handling contract using a minimal
async generator harness instead of instantiating a full
ConversationRuntime (which needs 15+ collaborators). The production
code path under test is the same.
"""
from __future__ import annotations

import asyncio

import pytest

from llm_code.runtime.hooks import _EVENT_GROUP, _event_matches


# ---------- Hook registration ----------

def test_tool_cancelled_event_registered() -> None:
    assert _EVENT_GROUP["tool_cancelled"] == "tool.tool_cancelled"


def test_tool_cancelled_matches_tool_glob() -> None:
    assert _event_matches("tool.*", "tool_cancelled") is True


def test_tool_cancelled_exact_match() -> None:
    assert _event_matches("tool_cancelled", "tool_cancelled") is True
    assert _event_matches("tool_cancelled", "tool_error") is False


# ---------- Cancellation contract (minimal harness) ----------

class _FakeHookRunner:
    def __init__(self) -> None:
        self.fired: list[tuple[str, dict]] = []

    def fire(self, event: str, payload: dict) -> None:
        self.fired.append((event, payload))


class _FakeToolResultBlock:
    def __init__(self, tool_use_id: str, content: str, is_error: bool) -> None:
        self.tool_use_id = tool_use_id
        self.content = content
        self.is_error = is_error


async def _cancellable_tool_generator(
    hook_runner: _FakeHookRunner,
    tool_name: str,
    tool_id: str,
):
    """Mirror of the production wave2-1d try/except pattern.

    The real ``_execute_tool_with_streaming`` wraps a ``while True:
    await queue.get()`` drain plus ``await future`` inside a
    try/except asyncio.CancelledError. This harness reproduces the
    exact same structure so we can test the cancellation contract
    without standing up the full runtime.
    """
    queue: asyncio.Queue = asyncio.Queue()
    fire_hook = lambda event, payload: hook_runner.fire(event, payload)  # noqa: E731

    try:
        while True:
            progress = await queue.get()
            if progress is None:
                break
            yield progress  # pragma: no cover — test never pushes progress
        yield "done"  # pragma: no cover
    except asyncio.CancelledError:
        fire_hook("tool_cancelled", {"tool_name": tool_name, "tool_id": tool_id})
        yield _FakeToolResultBlock(
            tool_use_id=tool_id,
            content=f"Tool '{tool_name}' execution was cancelled.",
            is_error=True,
        )
        raise


@pytest.mark.asyncio
async def test_cancellation_yields_error_block_then_reraises() -> None:
    """Canceling the consumer task must: (1) fire the hook, (2) yield
    an is_error ToolResultBlock, (3) re-raise CancelledError so the
    parent task can clean up. The yield-then-raise order is load-
    bearing: without it the session would record no terminal block
    for the interrupted call."""
    hook_runner = _FakeHookRunner()
    collected: list = []

    async def consume() -> None:
        gen = _cancellable_tool_generator(hook_runner, "bash", "call-1")
        async for item in gen:
            collected.append(item)

    task = asyncio.create_task(consume())
    # Yield control so the generator is inside `await queue.get()`
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # Hook fired exactly once with the tool metadata
    assert hook_runner.fired == [
        ("tool_cancelled", {"tool_name": "bash", "tool_id": "call-1"}),
    ]
    # Error ToolResultBlock was yielded before the re-raise
    assert len(collected) == 1
    assert isinstance(collected[0], _FakeToolResultBlock)
    assert collected[0].is_error is True
    assert collected[0].tool_use_id == "call-1"
    assert "cancelled" in collected[0].content.lower()


@pytest.mark.asyncio
async def test_cancellation_yields_error_block_with_tool_name() -> None:
    """The error content must include the tool name so a log reader
    can distinguish which tool was interrupted when multiple tools
    are in flight."""
    hook_runner = _FakeHookRunner()
    collected: list = []

    async def consume() -> None:
        gen = _cancellable_tool_generator(hook_runner, "my_custom_tool", "call-xyz")
        async for item in gen:
            collected.append(item)

    task = asyncio.create_task(consume())
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert "my_custom_tool" in collected[0].content


@pytest.mark.asyncio
async def test_cancellation_hook_payload_shape() -> None:
    """The hook payload must have exactly ``tool_name`` and
    ``tool_id`` keys so hook scripts can rely on the schema."""
    hook_runner = _FakeHookRunner()

    async def consume() -> None:
        gen = _cancellable_tool_generator(hook_runner, "read_file", "r1")
        async for _ in gen:
            pass

    task = asyncio.create_task(consume())
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    payload = hook_runner.fired[0][1]
    assert set(payload.keys()) == {"tool_name", "tool_id"}
    assert payload["tool_name"] == "read_file"
    assert payload["tool_id"] == "r1"


# ---------- Source-level guard ----------

def test_execute_tool_with_streaming_catches_cancelled_error() -> None:
    """The production code path must actually catch CancelledError.
    A future refactor that removes the try/except would silently
    break the cancellation contract; this source-probe catches it.

    We can't easily instantiate a full ConversationRuntime without
    15+ collaborators, so we inspect the method source directly —
    this is the same defensive pattern used by test_wave2_1c."""
    import inspect

    from llm_code.runtime.tool_pipeline import ToolExecutionPipeline

    src = inspect.getsource(ToolExecutionPipeline.execute_with_streaming)
    assert "except asyncio.CancelledError" in src
    # The yield-then-raise order: the raise must come after the yield
    idx_yield = src.find("Tool '{call.name}' execution was cancelled.")
    idx_raise = src.find("raise", idx_yield if idx_yield >= 0 else 0)
    assert idx_raise > idx_yield >= 0
    # tool_cancelled hook is fired in the handler
    assert '"tool_cancelled"' in src
