"""Persona executor should always clean up agent-owned MCP servers."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from llm_code.runtime.orchestrate_executor import inline_persona_executor
from llm_code.swarm.personas import AgentPersona


class _SpyMcpManager:
    def __init__(self) -> None:
        self.cleanup_calls: list[str] = []

    async def cleanup_for_agent(self, agent_id: str) -> list[str]:
        self.cleanup_calls.append(agent_id)
        return []


class _FakeResponse:
    def __init__(self, text: str) -> None:
        self.content = (SimpleNamespace(text=text),)


class _FakeProviderOk:
    async def send_message(self, _req: Any) -> _FakeResponse:
        return _FakeResponse("ok")


class _FakeProviderBoom:
    async def send_message(self, _req: Any) -> _FakeResponse:
        raise RuntimeError("boom")


def _runtime(provider: Any, mcp_manager: Any) -> Any:
    return SimpleNamespace(
        _provider=provider,
        _config=SimpleNamespace(model="test-model"),
        session=SimpleNamespace(session_id="sess-1"),
        _mcp_manager=mcp_manager,
    )


def _persona() -> AgentPersona:
    return AgentPersona(
        name="tester",
        description="test persona",
        system_prompt="you test",
        temperature=0.0,
    )


@pytest.mark.asyncio
async def test_cleanup_called_on_success() -> None:
    spy = _SpyMcpManager()
    runtime = _runtime(_FakeProviderOk(), spy)
    ok, text = await inline_persona_executor(runtime, _persona(), "do it")
    assert ok is True
    assert text == "ok"
    assert len(spy.cleanup_calls) == 1
    assert spy.cleanup_calls[0].startswith("persona-tester-")


@pytest.mark.asyncio
async def test_cleanup_called_on_failure() -> None:
    spy = _SpyMcpManager()
    runtime = _runtime(_FakeProviderBoom(), spy)
    ok, _ = await inline_persona_executor(runtime, _persona(), "do it")
    assert ok is False
    assert len(spy.cleanup_calls) == 1


@pytest.mark.asyncio
async def test_no_mcp_manager_is_noop() -> None:
    runtime = _runtime(_FakeProviderOk(), None)
    ok, text = await inline_persona_executor(runtime, _persona(), "do it")
    assert ok is True
    assert text == "ok"
