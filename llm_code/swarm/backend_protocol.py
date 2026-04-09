"""Protocol (interface) for swarm agent backends.

All three backends (SubprocessBackend, TmuxBackend, WorktreeBackend)
should satisfy this protocol so the ArenaManager can work with any
backend uniformly. This is a gradual migration — existing backends
already have the right shape, this formalizes it.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class AgentBackend(Protocol):
    """Common interface for swarm agent backends.

    Backends manage the lifecycle of spawned agent processes:
    create, communicate, and clean up.
    """

    async def spawn(
        self,
        member_id: str,
        prompt: str,
        *,
        role: str = "",
        model: str = "",
    ) -> str | int | None:
        """Spawn a new agent and return a process/session identifier."""
        ...

    async def stop(self, member_id: str) -> None:
        """Stop a running agent."""
        ...

    async def is_running(self, member_id: str) -> bool:
        """Check if an agent is still running."""
        ...

    async def send_message(self, member_id: str, message: str) -> None:
        """Send a message/prompt to a running agent."""
        ...

    async def get_output(self, member_id: str) -> str:
        """Get the latest output from an agent."""
        ...
