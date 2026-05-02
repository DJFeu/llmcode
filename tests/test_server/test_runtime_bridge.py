from __future__ import annotations

import pytest

from llm_code.api.types import (
    StreamMessageStop,
    StreamTextDelta,
    StreamToolExecResult,
    StreamToolExecStart,
    TokenUsage,
)
from llm_code.server.runtime_bridge import ServerRuntimeBridge


class _FakeManager:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def emit_event(self, session_id: str, payload: dict):
        self.events.append({"session_id": session_id, "payload": payload})


class _FakeRuntime:
    async def run_turn(self, text: str):
        assert text == "hello"
        yield StreamTextDelta("hi")
        yield StreamToolExecStart("read_file", "README.md")
        yield StreamToolExecResult("read_file", "ok")
        yield StreamMessageStop(TokenUsage(input_tokens=3, output_tokens=2), "end_turn")


@pytest.mark.asyncio
async def test_runtime_bridge_emits_formal_session_events() -> None:
    manager = _FakeManager()
    bridge = ServerRuntimeBridge(
        runtime=_FakeRuntime(),
        manager=manager,
        session_id="sess1",
    )

    await bridge.send_user_message("hello")

    payloads = [event["payload"] for event in manager.events]
    assert payloads[:5] == [
        {"type": "thinking_start"},
        {"type": "text_delta", "text": "hi"},
        {"type": "tool_start", "name": "read_file", "detail": "README.md"},
        {"type": "tool_result", "name": "read_file", "output": "ok", "isError": False},
        {"type": "text_done", "text": ""},
    ]
    assert payloads[5]["type"] == "thinking_stop"
    assert payloads[5]["elapsed"] >= 0
    assert payloads[5]["tokens"] == 2
    assert payloads[6] == {"type": "turn_done"}


@pytest.mark.asyncio
async def test_runtime_bridge_fork_emits_to_child_session() -> None:
    manager = _FakeManager()
    bridge = ServerRuntimeBridge(
        runtime=_FakeRuntime(),
        manager=manager,
        session_id="parent",
    )

    forked = bridge.fork_for_session("child")
    await forked.send_user_message("hello")

    assert {event["session_id"] for event in manager.events} == {"child"}
