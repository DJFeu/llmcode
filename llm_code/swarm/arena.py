"""Arena multi-agent manager — parallel agent coordination with backend abstraction.

Inspired by qwen-code's ArenaManager pattern. The Arena manages a set of
parallel agents, each running on a pluggable backend (subprocess, tmux,
worktree, or in-process). The manager handles:

- Spawning agents with specific roles and prompts
- Collecting results from all agents
- Coordinating shutdown and cleanup
- Backend auto-detection (best available)

Usage::

    arena = ArenaManager(backend)
    await arena.spawn("analyst", "Analyze the codebase structure")
    await arena.spawn("planner", "Create an implementation plan")
    results = await arena.collect_all(timeout=60)
    await arena.shutdown()
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

_log = logging.getLogger(__name__)


@dataclass
class ArenaAgent:
    """Tracks a single agent in the arena."""

    member_id: str
    role: str
    prompt: str
    started_at: float = field(default_factory=time.monotonic)
    result: str = ""
    is_done: bool = False
    error: str = ""


class ArenaManager:
    """Manages parallel agent execution across pluggable backends.

    The arena pattern decouples agent coordination (who runs what, when
    to collect results) from agent execution (how to spawn a process).
    This lets the same coordination logic work with tmux panes,
    subprocesses, worktrees, or in-process agents.
    """

    def __init__(self, backend: object) -> None:
        self._backend = backend
        self._agents: dict[str, ArenaAgent] = {}

    @property
    def active_count(self) -> int:
        return sum(1 for a in self._agents.values() if not a.is_done)

    @property
    def all_done(self) -> bool:
        return all(a.is_done for a in self._agents.values())

    async def spawn(
        self,
        member_id: str,
        prompt: str,
        *,
        role: str = "",
        model: str = "",
    ) -> None:
        """Spawn an agent in the arena."""
        agent = ArenaAgent(member_id=member_id, role=role, prompt=prompt)
        self._agents[member_id] = agent

        try:
            await self._backend.spawn(  # type: ignore[union-attr]
                member_id, prompt, role=role, model=model,
            )
            _log.info("arena: spawned agent %s (role=%s)", member_id, role)
        except Exception as exc:
            agent.error = str(exc)
            agent.is_done = True
            _log.warning("arena: failed to spawn %s: %s", member_id, exc)

    async def collect(self, member_id: str) -> str:
        """Collect output from a specific agent."""
        agent = self._agents.get(member_id)
        if agent is None:
            return ""
        try:
            output = await self._backend.get_output(member_id)  # type: ignore[union-attr]
            agent.result = output
            # Check if still running
            running = await self._backend.is_running(member_id)  # type: ignore[union-attr]
            agent.is_done = not running
            return output
        except Exception as exc:
            agent.error = str(exc)
            agent.is_done = True
            return ""

    async def collect_all(self, *, timeout: float = 120) -> dict[str, str]:
        """Wait for all agents to finish and return their outputs.

        Returns a dict of member_id → output. Agents that time out
        or error get empty strings.
        """
        deadline = time.monotonic() + timeout
        results: dict[str, str] = {}

        while not self.all_done:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _log.warning("arena: timeout waiting for %d agents", self.active_count)
                break
            for mid, agent in self._agents.items():
                if not agent.is_done:
                    await self.collect(mid)
            if not self.all_done:
                await asyncio.sleep(1.0)

        for mid, agent in self._agents.items():
            results[mid] = agent.result
        return results

    async def stop(self, member_id: str) -> None:
        """Stop a specific agent."""
        agent = self._agents.get(member_id)
        if agent is None:
            return
        try:
            await self._backend.stop(member_id)  # type: ignore[union-attr]
            agent.is_done = True
        except Exception as exc:
            _log.debug("arena: stop %s failed: %s", member_id, exc)
            agent.is_done = True

    async def shutdown(self) -> None:
        """Stop all agents and clean up."""
        for mid in list(self._agents.keys()):
            await self.stop(mid)
        self._agents.clear()
        _log.info("arena: all agents shut down")

    def summary(self) -> str:
        """Human-readable summary of arena state."""
        lines = [f"Arena: {len(self._agents)} agents ({self.active_count} active)"]
        for mid, agent in self._agents.items():
            status = "running" if not agent.is_done else ("error" if agent.error else "done")
            elapsed = time.monotonic() - agent.started_at
            lines.append(f"  {mid} [{agent.role or 'default'}]: {status} ({elapsed:.0f}s)")
        return "\n".join(lines)
