"""Tests for ``llm_code.hayhooks.session.HayhooksSession``."""
from __future__ import annotations


import pytest

from llm_code.hayhooks.session import (
    AgentResult,
    HayhooksSession,
    RateLimitExceeded,
    _coerce_result,
)


class TestSessionRun:
    def test_run_delegates_to_agent(self, session_factory, mock_agent):
        session = session_factory()
        result = session.run([{"role": "user", "content": "hi"}])
        assert isinstance(result, AgentResult)
        assert result.text == mock_agent.text
        assert mock_agent.calls, "agent.run was not invoked"

    def test_run_passes_max_steps_cap(self, hayhooks_config, mock_agent, session_factory):
        session = session_factory()
        session.run([{"role": "user", "content": "hi"}])
        assert mock_agent.calls[0]["max_steps"] == hayhooks_config.max_agent_steps

    def test_run_passes_allowed_tools_filter(self, hayhooks_config, mock_agent):
        cfg = hayhooks_config
        cfg.allowed_tools = ("read_file", "bash")
        session = HayhooksSession(config=cfg, agent=mock_agent)
        session.run([{"role": "user", "content": "hi"}])
        assert mock_agent.calls[0]["allowed_tools"] == ("read_file", "bash")

    def test_unique_session_ids(self, session_factory):
        a = session_factory()
        b = session_factory()
        assert a.session_id != b.session_id

    def test_metrics_recorded(self, session_factory):
        session = session_factory()
        session.run([{"role": "user", "content": "hi"}])
        assert len(session.metrics) == 1
        m = session.metrics[0]
        assert m.total_tokens == m.prompt_tokens + m.completion_tokens

    def test_rate_limit_blocks_after_quota(self, hayhooks_config, mock_agent):
        cfg = hayhooks_config
        cfg.rate_limit_rpm = 2
        session = HayhooksSession(config=cfg, agent=mock_agent)
        session.run([{"role": "user", "content": "hi"}])
        session.run([{"role": "user", "content": "hi"}])
        with pytest.raises(RateLimitExceeded):
            session.run([{"role": "user", "content": "hi"}])

    def test_rate_limit_disabled_when_zero(self, hayhooks_config, mock_agent):
        cfg = hayhooks_config
        cfg.rate_limit_rpm = 0
        session = HayhooksSession(config=cfg, agent=mock_agent)
        for _ in range(100):
            session.check_rate_limit()

    def test_rate_limit_window_slides(self, hayhooks_config, mock_agent):
        cfg = hayhooks_config
        cfg.rate_limit_rpm = 1
        session = HayhooksSession(config=cfg, agent=mock_agent)
        session.check_rate_limit(now=0.0)
        with pytest.raises(RateLimitExceeded):
            session.check_rate_limit(now=10.0)
        # After the 60s window passes, the slot frees up again.
        session.check_rate_limit(now=120.0)


class TestSessionAsync:
    async def test_run_async(self, session_factory, mock_agent):
        session = session_factory()
        result = await session.run_async([{"role": "user", "content": "hi"}])
        assert result.text == mock_agent.text

    async def test_run_async_falls_back_to_sync(self, hayhooks_config):
        """A sync-only agent is bridged via asyncio.to_thread."""

        class _SyncOnly:
            def run(self, messages, *, max_steps=20, allowed_tools=()):
                return AgentResult(text="sync-path")

        session = HayhooksSession(config=hayhooks_config, agent=_SyncOnly())
        result = await session.run_async([{"role": "user", "content": "hi"}])
        assert result.text == "sync-path"


class TestSessionStreaming:
    async def test_run_streaming_yields_events(self, session_factory, mock_agent):
        mock_agent.stream_events = [
            {"type": "text_delta", "text": "hello "},
            {"type": "text_delta", "text": "world"},
        ]
        session = session_factory()
        events = []
        async for evt in session.run_streaming([{"role": "user", "content": "hi"}]):
            events.append(evt)
        assert any(e.get("type") == "text_delta" for e in events)
        assert events[-1]["type"] == "done"


class TestCoerceResult:
    def test_passes_through_agent_result(self):
        ar = AgentResult(text="x")
        assert _coerce_result(ar) is ar

    def test_coerces_none(self):
        ar = _coerce_result(None)
        assert ar.text == ""

    def test_coerces_string(self):
        ar = _coerce_result("direct text")
        assert ar.text == "direct text"

    def test_coerces_duck_typed_object(self):
        class _Duck:
            text = "duck text"
            exit_reason = "length"
            prompt_tokens = 10
            completion_tokens = 20
            steps = 3

        ar = _coerce_result(_Duck())
        assert ar.text == "duck text"
        assert ar.exit_reason == "length"
        assert ar.prompt_tokens == 10
        assert ar.completion_tokens == 20


class TestAgentOptional:
    def test_agent_import_guarded(self):
        """The M4 module must import cleanly even if M3 Agent isn't ready."""
        from llm_code.hayhooks import session as session_mod
        # Agent may be None until M3 ships; the module should still load.
        assert hasattr(session_mod, "HayhooksSession")
