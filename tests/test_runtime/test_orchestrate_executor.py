"""Tests for the inline persona executor and OrchestratorHook integration."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from llm_code.runtime.orchestrate_executor import (
    inline_persona_executor,
    make_inline_persona_executor,
    sync_wrap,
)
from llm_code.swarm.orchestrator_hook import OrchestratorHook
from llm_code.swarm.personas import AgentPersona


@dataclass
class _TextBlock:
    text: str


class _Resp:
    def __init__(self, text: str) -> None:
        self.content = (_TextBlock(text=text),)


class _OkProvider:
    def __init__(self, text: str = "hello world") -> None:
        self._text = text
        self.last_request: Any = None

    async def send_message(self, request: Any) -> _Resp:
        self.last_request = request
        return _Resp(self._text)


class _BoomProvider:
    async def send_message(self, request: Any) -> _Resp:
        raise RuntimeError("boom")


def _runtime(provider: Any) -> Any:
    return SimpleNamespace(_provider=provider, _config=SimpleNamespace(model="m1"))


def _persona(name: str = "oracle") -> AgentPersona:
    return AgentPersona(
        name=name,
        description="d",
        system_prompt="be wise",
        temperature=0.3,
    )


def test_inline_runner_returns_success_with_text() -> None:
    rt = _runtime(_OkProvider("answered"))
    ok, text = asyncio.run(inline_persona_executor(rt, _persona(), "do thing"))
    assert ok is True
    assert text == "answered"
    req = rt._provider.last_request
    assert req.system == "be wise"
    assert req.temperature == 0.3
    assert req.model == "m1"


def test_inline_runner_returns_failure_tuple_on_exception() -> None:
    rt = _runtime(_BoomProvider())
    ok, err = asyncio.run(inline_persona_executor(rt, _persona(), "do thing"))
    assert ok is False
    assert "boom" in err


def test_orchestrator_hook_cycles_personas_on_failure() -> None:
    """First two personas fail, third succeeds — hook must walk the chain."""
    calls: list[str] = []

    def _runner(persona: AgentPersona, task: str) -> tuple[bool, str]:
        calls.append(persona.name)
        if len(calls) < 3:
            return False, f"{persona.name} failed"
        return True, f"{persona.name} ok"

    hook = OrchestratorHook(executor=_runner)
    result = hook.orchestrate("please refactor this module")
    assert result.success is True
    assert len(calls) == 3
    assert result.final_output.endswith("ok")
    assert result.attempts[-1].success is True


def test_final_output_is_first_successful_persona_text() -> None:
    provider = _OkProvider("the answer")
    rt = _runtime(provider)
    hook = OrchestratorHook(executor=sync_wrap(make_inline_persona_executor(rt)))
    result = hook.orchestrate("explain this code")
    assert result.success is True
    assert result.final_output == "the answer"
    assert result.attempts[0].success is True
