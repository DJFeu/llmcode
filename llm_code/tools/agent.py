"""AgentTool — spawns a sub-agent runtime to handle a delegated sub-task.

Supports two execution modes:

1. **Single agent** (default): Spawns one sub-agent with a given role.
   Recursion depth is bounded by ``max_depth``.

2. **Parallel fork** (``fork_directives``): Spawns N children in parallel.
   All children share a byte-identical API prefix for prompt-cache
   sharing (Anthropic).  On other providers the fork still works — the
   children just don't get cache savings.

Risk mitigations:
    - ``is_in_fork_child()`` prevents recursive forking even though the
      ``agent`` tool is kept in the child's tool pool (cache parity).
    - ``max_depth`` guards standard recursion for non-fork paths.
    - ``asyncio.gather`` with ``return_exceptions=True`` prevents one
      failing child from crashing siblings.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import inspect
from typing import Any, Callable

from pydantic import BaseModel

from llm_code.api.types import StreamTextDelta
from llm_code.tools.agent_roles import BUILT_IN_ROLES, AgentRole
from llm_code.tools.base import PermissionLevel, Tool, ToolResult


def _registry_names() -> tuple[str, ...]:
    """Return the dynamic enum from :mod:`runtime.agent_registry`.

    Falls back to the built-in five-role list if the registry is
    unavailable (legacy import path or extreme-early init). The
    fallback never breaks tests — it just collapses to the v2.5.5
    behaviour.
    """
    try:
        from llm_code.runtime.agent_registry import get_registry
    except ImportError:  # pragma: no cover — defensive
        return tuple(BUILT_IN_ROLES.keys())
    names = get_registry().list_names()
    return names if names else tuple(BUILT_IN_ROLES.keys())


class AgentInput(BaseModel):
    task: str
    model: str = ""
    role: str = ""


class AgentTool(Tool):
    """Spawn a sub-agent to handle a sub-task, up to max_depth levels deep."""

    def __init__(
        self,
        runtime_factory: Callable,
        max_depth: int = 3,
        current_depth: int = 0,
    ) -> None:
        self._runtime_factory = runtime_factory
        self._max_depth = max_depth
        self._current_depth = current_depth

    # ------------------------------------------------------------------
    # Tool interface
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "agent"

    @property
    def description(self) -> str:
        return (
            "Spawn a sub-agent to handle a sub-task. "
            "Use fork_directives for parallel tasks sharing prompt cache."
        )

    @property
    def input_schema(self) -> dict:
        # v16 M1: enum is built dynamically from the AgentRegistry so
        # user-defined roles in ``.llm-code/agents/*.md`` show up in
        # the LLM's tool schema. The schema is rebuilt every turn
        # (existing tool-definition flow), so registry changes never
        # surface as a stale enum.
        role_names = list(_registry_names())
        return {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "Task for the sub-agent (single mode)",
                },
                "model": {
                    "type": "string",
                    "description": "Optional model override",
                },
                "role": {
                    "type": "string",
                    "enum": role_names,
                    "description": (
                        "Agent role from the registry. Built-ins: 'build' "
                        "(default, unrestricted), 'plan'/'explore' (read-only), "
                        "'verify' (adversarial), 'general' (focused subagent). "
                        "User-defined roles from .llm-code/agents/*.md also appear here."
                    ),
                },
                "fork_directives": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "List of directives for parallel fork children. "
                        "All children share prompt cache prefix for cost "
                        "savings. Mutually exclusive with 'task'."
                    ),
                },
            },
            # Neither task nor fork_directives is strictly required —
            # validation happens in execute().
        }

    @property
    def required_permission(self) -> PermissionLevel:
        return PermissionLevel.READ_ONLY

    def is_concurrency_safe(self, args: dict) -> bool:
        # Each sub-agent has its own session
        return True

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def execute(self, args: dict) -> ToolResult:
        if self._current_depth >= self._max_depth:
            return ToolResult(
                output=f"Max agent depth reached ({self._max_depth})",
                is_error=True,
            )

        fork_directives: list[str] | None = args.get("fork_directives")
        task: str = args.get("task", "")

        # Mutual exclusion
        if fork_directives and task:
            return ToolResult(
                output="Provide either 'task' or 'fork_directives', not both.",
                is_error=True,
            )
        if not fork_directives and not task:
            return ToolResult(
                output="Provide 'task' (single agent) or 'fork_directives' (parallel fork).",
                is_error=True,
            )

        model: str | None = args.get("model") or None
        role_name: str = args.get("role", "")

        # Resolve role via the dynamic registry (M1) — built-ins +
        # user-defined .llm-code/agents/*.md files. Falls back to the
        # built-in dict if the registry is unavailable for any reason
        # (e.g. import-cycle during very-early init), preserving the
        # v2.5.5 behaviour.
        role: AgentRole | None = None
        if role_name:
            try:
                from llm_code.runtime.agent_registry import get_registry
                role = get_registry().resolve(role_name)
            except ImportError:  # pragma: no cover — defensive
                role = None
            if role is None:
                role = BUILT_IN_ROLES.get(role_name)
            if role is None:
                valid = list(_registry_names())
                return ToolResult(
                    output=f"Unknown role: '{role_name}'. Valid roles: {valid}",
                    is_error=True,
                )

        # Sync wrapper: works whether or not an event loop is running
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if fork_directives:
            coro = self._execute_parallel_fork(fork_directives, model, role)
        else:
            coro = self._execute_async(task, model, role)

        if loop and loop.is_running():
            with concurrent.futures.ThreadPoolExecutor() as pool:
                result = pool.submit(asyncio.run, coro).result()
        else:
            result = asyncio.run(coro)

        return result

    def _call_factory(self, model: str | None, role: AgentRole | None):
        """Call runtime_factory, passing role= only if the factory accepts it."""
        try:
            sig = inspect.signature(self._runtime_factory)
            params = sig.parameters
            accepts_role = (
                "role" in params
                or any(
                    p.kind == inspect.Parameter.VAR_KEYWORD
                    for p in params.values()
                )
            )
        except (ValueError, TypeError):
            accepts_role = False

        if accepts_role:
            return self._runtime_factory(model, role=role)
        return self._runtime_factory(model)

    async def _execute_async(
        self, task: str, model: str | None, role: AgentRole | None
    ) -> ToolResult:
        runtime = self._call_factory(model, role)
        collected: list[str] = []
        async for event in runtime.run_turn(task):
            if isinstance(event, StreamTextDelta):
                collected.append(event.text)
        return ToolResult(output="".join(collected) or "(no output)")

    # ------------------------------------------------------------------
    # Parallel fork
    # ------------------------------------------------------------------

    async def _execute_parallel_fork(
        self,
        directives: list[str],
        model: str | None,
        role: AgentRole | None,
    ) -> ToolResult:
        """Execute multiple fork children in parallel.

        Each child gets a fresh runtime but the forked-message construction
        ensures byte-identical API prefixes for prompt-cache sharing on
        Anthropic.  On other providers the fork is still structurally
        correct — children just don't get cache savings.

        Risk mitigations:
            - ``return_exceptions=True`` in ``asyncio.gather`` isolates
              failures so one crashing child doesn't kill siblings.
            - ``is_in_fork_child()`` check on the runtime's message
              history prevents recursive forks.
        """
        if not directives:
            return ToolResult(output="No directives provided", is_error=True)
        if len(directives) > 10:
            return ToolResult(
                output=f"Too many fork children ({len(directives)}); max 10.",
                is_error=True,
            )

        tasks = [
            self._run_single_fork_child(directive, model, role)
            for directive in directives
        ]
        results: list[ToolResult | BaseException] = await asyncio.gather(
            *tasks, return_exceptions=True,
        )

        # Aggregate results
        parts: list[str] = []
        for i, res in enumerate(results):
            header = f"--- Fork child {i + 1}/{len(directives)} ---"
            if isinstance(res, BaseException):
                parts.append(f"{header}\n[ERROR] {type(res).__name__}: {res}")
            else:
                parts.append(f"{header}\n{res.output}")

        return ToolResult(output="\n\n".join(parts))

    async def _run_single_fork_child(
        self,
        directive: str,
        model: str | None,
        role: AgentRole | None,
    ) -> ToolResult:
        """Run one fork child.  Structurally identical to _execute_async
        but uses the fork boilerplate as the task prompt."""
        from llm_code.runtime.fork_cache import (
            build_child_message,
            is_in_fork_child,
        )

        runtime = self._call_factory(model, role)

        # Recursion guard: check if we're already inside a fork child
        history: list[dict[str, Any]] = []
        session = getattr(runtime, "session", None)
        if session is not None:
            history = getattr(session, "messages", [])
        if is_in_fork_child(history):
            return ToolResult(
                output="Cannot fork from within a fork child.",
                is_error=True,
            )

        # The child's task IS the boilerplate + directive.
        # On providers with prompt caching, the system prompt + tool
        # definitions are already cached from the parent.  The child
        # message itself is new but small.
        child_task = build_child_message(directive)

        collected: list[str] = []
        async for event in runtime.run_turn(child_task):
            if isinstance(event, StreamTextDelta):
                collected.append(event.text)

        return ToolResult(output="".join(collected) or "(no output)")
