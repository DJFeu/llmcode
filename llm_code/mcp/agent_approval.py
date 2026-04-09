"""Per-agent MCP server approval and ownership tracking.

When a sub-agent declares MCP servers in its frontmatter, the parent runtime
must:

1. Present an approval request to the user (UI layer — not owned here).
2. If approved, spawn the declared servers and record which agent owns them.
3. On agent exit, clean up *only* the servers that agent owns — leaving other
   agents' servers running.

This module owns the ownership bookkeeping and the approval-request shape.
Wired into ``llm_code.mcp.manager.MCPManager`` via the ``approval_callback``
parameter on ``start_server()``. The ``ConversationRuntime.request_mcp_approval``
method serves as the callback, surfacing requests to the TUI via
``StreamMCPApprovalRequest`` events (or the modal dialog in TextualDialogs mode).
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Iterable

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MCPApprovalRequest:
    """Request to spawn MCP servers on behalf of an agent."""

    agent_id: str
    agent_name: str
    server_names: tuple[str, ...]
    reason: str = ""

    def summary(self) -> str:
        servers = ", ".join(self.server_names) or "(none)"
        return f"Agent {self.agent_name!r} requests MCP servers: {servers}"


@dataclass
class _Ownership:
    agent_id: str
    servers: set[str] = field(default_factory=set)


class AgentMCPRegistry:
    """Track which MCP servers were spawned for which agent.

    Thread-safe. Multiple agents may own the same server name only if they
    refer to distinct server instances — this registry records one set per
    agent and the cleanup routine only acts on that agent's set.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._owners: dict[str, _Ownership] = {}

    def track_owner(self, agent_id: str, server_names: Iterable[str]) -> None:
        """Record that *agent_id* owns the given server names."""
        with self._lock:
            entry = self._owners.setdefault(agent_id, _Ownership(agent_id=agent_id))
            entry.servers.update(server_names)

    def owned_by(self, agent_id: str) -> frozenset[str]:
        with self._lock:
            entry = self._owners.get(agent_id)
            if entry is None:
                return frozenset()
            return frozenset(entry.servers)

    def cleanup_owned_servers(
        self,
        agent_id: str,
        shutdown: "object | None" = None,
    ) -> list[str]:
        """Remove ownership records for *agent_id* and optionally call *shutdown*.

        *shutdown* (if provided) is invoked as ``shutdown(server_name)`` for
        each server owned by the agent. Exceptions from shutdown are logged
        but do not stop the cleanup loop.

        Returns the list of server names that were cleaned up.
        """
        with self._lock:
            entry = self._owners.pop(agent_id, None)
            owned = list(entry.servers) if entry else []
        if shutdown is not None:
            for name in owned:
                try:
                    shutdown(name)  # type: ignore[misc]
                except Exception:  # pragma: no cover - defensive
                    logger.exception("mcp cleanup: shutdown(%s) failed", name)
        return owned

    def all_agents(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._owners.keys())
