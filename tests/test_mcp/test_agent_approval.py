"""Tests for per-agent MCP server approval and ownership tracking."""
from __future__ import annotations

from llm_code.mcp.agent_approval import AgentMCPRegistry, MCPApprovalRequest


class TestApprovalRequest:
    def test_shape(self) -> None:
        req = MCPApprovalRequest(
            agent_id="a1",
            agent_name="reviewer",
            server_names=("fs", "github"),
            reason="code review needs repo + fs",
        )
        assert req.server_names == ("fs", "github")
        assert "reviewer" in req.summary()
        assert "fs" in req.summary()

    def test_empty_servers_summary(self) -> None:
        req = MCPApprovalRequest(agent_id="a", agent_name="n", server_names=())
        assert "(none)" in req.summary()


class TestAgentMCPRegistry:
    def test_track_and_lookup(self) -> None:
        reg = AgentMCPRegistry()
        reg.track_owner("agent-1", ["fs", "github"])
        assert reg.owned_by("agent-1") == frozenset({"fs", "github"})
        assert reg.owned_by("agent-unknown") == frozenset()

    def test_track_accumulates(self) -> None:
        reg = AgentMCPRegistry()
        reg.track_owner("a", ["x"])
        reg.track_owner("a", ["y"])
        assert reg.owned_by("a") == frozenset({"x", "y"})

    def test_cleanup_calls_shutdown_and_clears(self) -> None:
        reg = AgentMCPRegistry()
        reg.track_owner("a", ["s1", "s2"])
        stopped: list[str] = []
        cleaned = reg.cleanup_owned_servers("a", shutdown=stopped.append)
        assert sorted(cleaned) == ["s1", "s2"]
        assert sorted(stopped) == ["s1", "s2"]
        assert reg.owned_by("a") == frozenset()

    def test_cleanup_only_removes_owned(self) -> None:
        reg = AgentMCPRegistry()
        reg.track_owner("a", ["s1"])
        reg.track_owner("b", ["s2"])
        reg.cleanup_owned_servers("a")
        assert reg.owned_by("a") == frozenset()
        assert reg.owned_by("b") == frozenset({"s2"})

    def test_shutdown_exception_does_not_stop_loop(self) -> None:
        reg = AgentMCPRegistry()
        reg.track_owner("a", ["good", "bad", "also_good"])

        def shutdown(name: str) -> None:
            if name == "bad":
                raise RuntimeError("oops")

        cleaned = reg.cleanup_owned_servers("a", shutdown=shutdown)
        assert set(cleaned) == {"good", "bad", "also_good"}

    def test_all_agents(self) -> None:
        reg = AgentMCPRegistry()
        reg.track_owner("a", ["x"])
        reg.track_owner("b", ["y"])
        assert set(reg.all_agents()) == {"a", "b"}
