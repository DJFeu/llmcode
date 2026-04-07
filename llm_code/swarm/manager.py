"""SwarmManager — orchestrate creation, lifecycle, and teardown of swarm members."""
from __future__ import annotations

import re
import subprocess as sp
import uuid
from pathlib import Path

from llm_code.runtime.config import RuntimeConfig
from llm_code.swarm.backend_subprocess import SubprocessBackend
from llm_code.swarm.backend_tmux import TmuxBackend, is_tmux_available
from llm_code.swarm.backend_worktree import WorktreeBackend
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
        config: RuntimeConfig | None = None,
    ) -> None:
        self._swarm_dir = Path(swarm_dir)
        self._swarm_dir.mkdir(parents=True, exist_ok=True)
        self._max_members = max_members
        self._backend_preference = backend_preference
        self._members: dict[str, SwarmMember] = {}
        self._config = config or RuntimeConfig()

        # Backends (lazily used)
        self._subprocess_backend = SubprocessBackend(swarm_dir=self._swarm_dir)
        self._tmux_backend = TmuxBackend()
        # WorktreeBackend is initialised on demand (requires git + project dir)
        self._worktree_backend: WorktreeBackend | None = None

        # Shared resources
        self.mailbox = Mailbox(self._swarm_dir / "mailbox")
        self.shared_memory = SharedMemory(self._swarm_dir / "memory.json")

    async def create_member(
        self,
        role: str,
        task: str,
        backend: str = "auto",
        model: str | None = None,
        persona: str = "",
    ) -> SwarmMember:
        """Spawn a new swarm worker.

        Args:
            role: Role description (e.g. 'security reviewer').
            task: The task this member should perform.
            backend: 'tmux', 'subprocess', or 'auto' (default).
            model: Override the model for this specific member. When None,
                the effective model is resolved via the 4-level fallback chain.

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

        # Apply persona overrides if requested.
        persona_obj = None
        if persona:
            from llm_code.swarm.personas import BUILTIN_PERSONAS

            persona_obj = BUILTIN_PERSONAS.get(persona)
            if persona_obj is None:
                raise ValueError(f"Unknown persona: {persona}")
            # Persona system prompt is prepended to the task description.
            task = f"{persona_obj.system_prompt}\n\n---\n\nTASK: {task}"

        effective_model = self._resolve_model(role, model, persona_obj)

        pid: int | str | None = None
        if effective_backend == "tmux":
            pid = self._tmux_backend.spawn(
                member_id=member_id, role=role, task=task, model=effective_model,
            )
        elif effective_backend == "worktree":
            if self._worktree_backend is None:
                self._worktree_backend = WorktreeBackend(
                    project_dir=self._swarm_dir.parent,
                    config=self._config.swarm.worktree,
                )
            pid = await self._worktree_backend.spawn(
                member_id=member_id, role=role, task=task, model=effective_model,
            )
        else:
            pid = await self._subprocess_backend.spawn(
                member_id=member_id, role=role, task=task, model=effective_model,
            )

        member = SwarmMember(
            id=member_id,
            role=role,
            task=task,
            backend=effective_backend,
            pid=pid if isinstance(pid, int) else None,
            status=SwarmStatus.RUNNING,
            model=effective_model,
        )
        self._members[member_id] = member
        return member

    def _resolve_model(self, role: str, explicit: str | None, persona=None) -> str:
        """Determine the effective model using a 5-level fallback chain.

        Priority (highest to lowest):
          1. explicit argument
          2. persona.model_hint resolved against config.model_routing
          3. config.swarm.role_models[role]
          4. config.model_routing.sub_agent
          5. config.model

        The resolved value is then looked up in config.model_aliases.
        """
        if explicit:
            model = explicit
        elif persona is not None and persona.model_hint:
            routing = self._config.model_routing
            hint = persona.model_hint
            model = (
                getattr(routing, hint, None)
                or getattr(routing, "sub_agent", None)
                or self._config.model
            )
        elif role in self._config.swarm.role_models:
            model = self._config.swarm.role_models[role]
        elif self._config.model_routing.sub_agent:
            model = self._config.model_routing.sub_agent
        else:
            model = self._config.model
        return self._config.model_aliases.get(model, model)

    def list_members(self) -> list[SwarmMember]:
        """Return all current swarm members."""
        return list(self._members.values())

    def get_member(self, member_id: str) -> SwarmMember | None:
        """Return a swarm member by id, or None if not found."""
        return self._members.get(member_id)

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
        """Determine which backend to use.

        Priority for explicit requests: worktree > tmux > subprocess.
        In auto mode: worktree (if git available) > tmux (if available) > subprocess.
        """
        if requested == "worktree":
            return "worktree"
        if requested == "tmux":
            return "tmux"
        if requested == "subprocess":
            return "subprocess"
        # auto path — honour backend_preference first
        pref = self._backend_preference
        if pref == "worktree":
            return "worktree"
        if pref == "tmux":
            return "tmux"
        # pref == "auto": try worktree > tmux > subprocess
        if self._is_git_repo() and self._git_supports_worktree():
            return "worktree"
        return "tmux" if is_tmux_available() else "subprocess"

    def _is_git_repo(self) -> bool:
        """Return True if the project directory is inside a git repository."""
        result = sp.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=str(self._swarm_dir.parent),
            capture_output=True,
            text=True,
        )
        return result.returncode == 0

    def _git_supports_worktree(self) -> bool:
        """Return True if the installed git version supports worktrees (>= 2.15)."""
        result = sp.run(["git", "--version"], capture_output=True, text=True)
        if result.returncode != 0:
            return False
        match = re.search(r"(\d+)\.(\d+)", result.stdout)
        if not match:
            return False
        major, minor = int(match.group(1)), int(match.group(2))
        return (major, minor) >= (2, 15)
