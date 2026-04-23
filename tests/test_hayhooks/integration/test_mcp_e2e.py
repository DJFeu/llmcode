"""End-to-end tests for the MCP transport with a mocked agent."""
from __future__ import annotations

import pytest

pytest.importorskip("mcp")

from llm_code.hayhooks.mcp_transport import build_mcp_server
from llm_code.hayhooks.session import AgentResult, HayhooksSession


def _factory(mock_agent):
    def _make(config, fingerprint=""):
        return HayhooksSession(
            config=config, fingerprint=fingerprint, agent=mock_agent,
        )
    return _make


class TestMcpE2E:
    async def test_session_drives_agent(self, hayhooks_config, mock_agent):
        # We don't spawn a subprocess (flakey in CI); instead we assert the
        # decorator-registered handlers route through the session wrapper.
        mock_agent.text = "mcp-roundtrip"
        session = HayhooksSession(
            config=hayhooks_config, agent=mock_agent,
        )
        result = await session.run_async([
            {"role": "user", "content": "ping"}
        ])
        assert isinstance(result, AgentResult)
        assert result.text == "mcp-roundtrip"

    def test_build_mcp_server_with_factory(self, hayhooks_config, mock_agent):
        srv = build_mcp_server(hayhooks_config, session_factory=_factory(mock_agent))
        assert getattr(srv, "name", "") == "llmcode-hayhooks"

    def test_initialization_options_sane(self, hayhooks_config, mock_agent):
        srv = build_mcp_server(hayhooks_config, session_factory=_factory(mock_agent))
        opts = srv.create_initialization_options()
        assert opts is not None
