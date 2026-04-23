"""Shared hayhooks test fixtures."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from llm_code.hayhooks.session import AgentResult, HayhooksSession


@dataclass
class _MockAgent:
    """Synchronous + async test double for the M3 Agent."""

    text: str = "hello"
    exit_reason: str = "stop"
    prompt_tokens: int = 5
    completion_tokens: int = 3
    steps: int = 2
    calls: list[dict] = field(default_factory=list)
    raise_on_run: BaseException | None = None
    stream_events: list[dict] = field(default_factory=list)

    def _record(self, messages, max_steps, allowed_tools):
        self.calls.append({
            "messages": list(messages),
            "max_steps": max_steps,
            "allowed_tools": tuple(allowed_tools),
        })

    def run(self, messages, *, max_steps=20, allowed_tools=()):
        if self.raise_on_run:
            raise self.raise_on_run
        self._record(messages, max_steps, allowed_tools)
        return AgentResult(
            text=self.text,
            exit_reason=self.exit_reason,
            prompt_tokens=self.prompt_tokens,
            completion_tokens=self.completion_tokens,
            steps=self.steps,
        )

    async def run_async(self, messages, *, max_steps=20, allowed_tools=()):
        return self.run(messages, max_steps=max_steps, allowed_tools=allowed_tools)

    async def run_streaming(self, messages, *, max_steps=20, allowed_tools=()):
        self._record(messages, max_steps, allowed_tools)
        events = self.stream_events or [
            {"type": "text_delta", "text": self.text},
        ]
        for e in events:
            yield e
        yield {
            "type": "done",
            "result": AgentResult(
                text=self.text,
                exit_reason=self.exit_reason,
                prompt_tokens=self.prompt_tokens,
                completion_tokens=self.completion_tokens,
                steps=self.steps,
            ),
        }


@pytest.fixture
def mock_agent():
    return _MockAgent()


@pytest.fixture
def hayhooks_config():
    """Minimal duck-typed HayhooksConfig — good enough for unit tests."""

    @dataclass
    class _Cfg:
        enabled: bool = True
        auth_token_env: str = "LLMCODE_HAYHOOKS_TOKEN"
        allowed_tools: tuple[str, ...] = ()
        max_agent_steps: int = 7
        request_timeout_s: float = 30.0
        rate_limit_rpm: int = 60
        enable_openai_compat: bool = True
        enable_mcp: bool = True
        enable_ide_rpc: bool = False
        enable_debug_repl: bool = False
        cors_origins: tuple[str, ...] = ()
        host: str = "127.0.0.1"
        port: int = 0

    return _Cfg()


@pytest.fixture
def session_factory(hayhooks_config, mock_agent):
    def _make(config: Any = None, fingerprint: str = "fp-test") -> HayhooksSession:
        return HayhooksSession(
            config=config or hayhooks_config,
            fingerprint=fingerprint,
            agent=mock_agent,
        )
    return _make


@pytest.fixture
def bearer_env(monkeypatch):
    monkeypatch.setenv("LLMCODE_HAYHOOKS_TOKEN", "secret-token-xyz")
    return "secret-token-xyz"
