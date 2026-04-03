"""SwarmManager — orchestrate creation, lifecycle, and teardown of swarm members."""
from __future__ import annotations

import uuid
from pathlib import Path

from llm_code.swarm.backend_subprocess import SubprocessBackend
from llm_code.swarm.backend_tmux import TmuxBackend, is_tmux_available
from llm_code.swarm.mailbox import Mailbox
from llm_code.swarm.memory_sync import SharedMemory
from llm_code.swarm.types import SwarmMember, SwarmStatus


class SwarmManager:
    """Manage the lifecycle of swarm worker agents.

    Auto-detects tmux (if available and inside a session), otherwise falls
    back to subprocess.  Each member is a llm-code --lite process with a
    role prompt injected at startup.
    """

    def __init__(
        self,
        swarm_dir: Path,
        max_members: int = 5,
        backend_preference: str = "auto",
    ) -> None:
        self._swarm_dir = Path(swarm_dir)
        self._swarm_dir.mkdir(parents=True, exist_ok=True)
        self._max_members = max_members
        self._backend_preference = backend_preference
        self._members: dict[str, SwarmMember] = {}

        # Backends (lazily used)
        self._subprocess_backend = SubprocessBackend(swarm_dir=self._swarm_dir)
        self._tmux_backend = TmuxBackend()

        # Shared resources
        self.mailbox = Mailbox(self._swarm_dir / "mailbox")
        self.shared_memory = SharedMemory(self._swarm_dir / "memory.json")

    async def create_member(
        self,
        role: str,
        task: str,
        backend: str = "auto",
    ) -> SwarmMember:
        """Spawn a new swarm worker.

        Args:
            role: Role description (e.g. 'security reviewer').
            task: The task this member should perform.
            backend: 'tmux', 'subprocess', or 'auto' (default).

        Returns:
            The created SwarmMember.

        Raises:
            ValueError: If max_members limit is reached.
        """
        if len(self._members) >= self._max_members:
            raise ValueError(
                f"Cannot create member: max {self._max_members} members reached"
            )

        member_id = uuid.uuid4().hex[:8]
        effective_backend = self._resolve_backend(backend)

        pid: int | str | None = None
        if effective_backend == "tmux":
            pid = self._tmux_backend.spawn(
                member_id=member_id, role=role, task=task,
            )
        else:
            pid = await self._subprocess_backend.spawn(
                member_id=member_id, role=role, task=task,
            )

        member = SwarmMember(
            id=member_id,
            role=role,
            task=task,
            backend=effective_backend,
            pid=pid if isinstance(pid, int) else None,
            status=SwarmStatus.RUNNING,
        )
        self._members[member_id] = member
        return member

    def list_members(self) -> list[SwarmMember]:
        """Return all current swarm members."""
        return list(self._members.values())

    async def stop_member(self, member_id: str) -> None:
        """Stop and remove a swarm member.

        Raises:
            KeyError: If member_id is not found.
        """
        member = self._members.get(member_id)
        if member is None:
            raise KeyError(f"No swarm member with id '{member_id}'")

        if member.backend == "tmux":
            self._tmux_backend.stop(member_id)
        else:
            await self._subprocess_backend.stop(member_id)

        del self._members[member_id]

    async def stop_all(self) -> None:
        """Stop all swarm members."""
        await self._subprocess_backend.stop_all()
        self._tmux_backend.stop_all()
        self._members.clear()

    def _resolve_backend(self, requested: str) -> str:
        """Determine which backend to use."""
        if requested == "tmux":
            return "tmux"
        if requested == "subprocess":
            return "subprocess"
        # auto: prefer tmux if available
        pref = self._backend_preference
        if pref == "auto":
            return "tmux" if is_tmux_available() else "subprocess"
        if pref == "tmux":
            return "tmux"
        return "subprocess"
