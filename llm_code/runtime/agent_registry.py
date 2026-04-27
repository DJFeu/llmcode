"""AgentRegistry — dynamic role catalogue for AgentTool.

v16 M1 — replaces the hardcoded ``role`` enum on
:class:`llm_code.tools.agent.AgentTool` with a registry-driven lookup so
that user-defined ``.llm-code/agents/*.md`` files become invocable via
``agent(role="...")``.

The registry is intentionally a small singleton holding immutable
``AgentRole`` values. Built-in roles are seeded eagerly at import time;
user roles are populated by an explicit discovery sweep at session
init.

Resolution order at lookup:
    1. Project agents       (``<project>/.llm-code/agents/*.md``)
    2. User-global agents   (``~/.llm-code/agents/*.md``)
    3. Built-in roles       (``BUILT_IN_ROLES``)

When a user-defined role shadows a built-in, the custom role wins and
a ``WARNING`` is logged so the override is visible in the log stream.

Risk mitigations
----------------

* The registry is intentionally re-entrant — a second call to
  :meth:`AgentRegistry.discover` overwrites the user/project layer
  rather than appending, so reload (`/reload`) yields a clean state.
* Discovery never raises if an agent file is malformed; the loader
  emits a warning and skips that file.
* The schema rebuild path on :class:`AgentTool` reads the registry
  every turn — the LLM never sees a stale enum even if the registry
  changes mid-session.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable

from llm_code.tools.agent_roles import BUILT_IN_ROLES, AgentRole

logger = logging.getLogger(__name__)


class AgentRegistry:
    """In-process catalogue of all known agent roles.

    Singleton-shaped (one shared instance returned by
    :func:`get_registry`), but the constructor is public so tests can
    instantiate isolated registries with their own discovery roots.
    """

    def __init__(self) -> None:
        # Layered storage: built-in always present, user/project layers
        # rebuilt on each ``discover`` call. ``_layered`` is the merged
        # view used by lookups.
        self._builtin: dict[str, AgentRole] = dict(BUILT_IN_ROLES)
        self._user: dict[str, AgentRole] = {}
        self._project: dict[str, AgentRole] = {}
        self._layered: dict[str, AgentRole] = dict(BUILT_IN_ROLES)

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def discover(self, project_path: Path | None = None) -> None:
        """(Re-)scan user and project agent directories.

        Built-in roles are never touched. After the call,
        :meth:`list_names` reflects the merged view.
        """
        # Lazy import to avoid a hard dependency loop with tools/.
        from llm_code.tools.agent_loader import _load_agents_from_dir

        user_dir = Path.home() / ".llm-code" / "agents"
        self._user = _load_agents_from_dir(user_dir)

        if project_path is not None:
            project_dir = project_path / ".llm-code" / "agents"
            self._project = _load_agents_from_dir(project_dir)
        else:
            self._project = {}

        self._rebuild_layered()

    def register(self, role: AgentRole) -> None:
        """Add or override an in-memory role.

        Useful for tests or for plugins that want to declare agents at
        runtime. The role lands in the user layer so a subsequent
        :meth:`discover` call rebuilds the merged view without losing
        it (project layer takes precedence on collision).
        """
        if not role.name:
            raise ValueError("AgentRole must have a non-empty name")
        self._user[role.name] = role
        self._rebuild_layered()

    def _rebuild_layered(self) -> None:
        """Recompute the merged view after a layer changes."""
        merged: dict[str, AgentRole] = dict(self._builtin)
        # User overrides built-in, project overrides both. Each layer
        # walks its own dict (so an empty layer cleanly contributes
        # nothing) and emits a warning on shadow.
        for name, role in self._user.items():
            if name in merged:
                logger.warning(
                    "user agent %r shadows built-in role with the same name",
                    name,
                )
            merged[name] = role
        for name, role in self._project.items():
            if name in merged and merged[name] is not role:
                logger.warning(
                    "project agent %r shadows existing role with the same name",
                    name,
                )
            merged[name] = role
        self._layered = merged

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def resolve(self, name: str) -> AgentRole | None:
        """Return the merged ``AgentRole`` for *name* or ``None``."""
        if not name:
            return None
        return self._layered.get(name)

    def list_names(self) -> tuple[str, ...]:
        """Return all known role names in lookup order.

        The list is ordered so that built-ins come first (stable for
        humans reading enums), followed by alphabetised custom roles.
        """
        builtin = [n for n in self._builtin if n in self._layered]
        custom = sorted(
            n for n in self._layered if n not in self._builtin
        )
        return tuple(builtin + custom)

    def list_roles(self) -> Iterable[AgentRole]:
        """Yield every merged role (built-in + custom)."""
        for name in self.list_names():
            yield self._layered[name]

    # ------------------------------------------------------------------
    # Test helpers
    # ------------------------------------------------------------------

    def reset(self) -> None:
        """Drop user/project layers; restore built-ins only.

        Test-only — keeps the singleton clean across pytest tmp_path
        fixtures so one test's ``register`` calls don't leak.
        """
        self._user = {}
        self._project = {}
        self._layered = dict(self._builtin)


_GLOBAL_REGISTRY: AgentRegistry | None = None


def get_registry() -> AgentRegistry:
    """Return the process-wide :class:`AgentRegistry`.

    Lazy construction — no I/O happens at import time. Callers must
    invoke :meth:`AgentRegistry.discover` once to populate user/project
    layers; subsequent lookups are pure in-memory.
    """
    global _GLOBAL_REGISTRY
    if _GLOBAL_REGISTRY is None:
        _GLOBAL_REGISTRY = AgentRegistry()
    return _GLOBAL_REGISTRY


def reset_registry() -> None:
    """Test-only: drop the singleton and the next ``get_registry`` rebuilds."""
    global _GLOBAL_REGISTRY
    _GLOBAL_REGISTRY = None
