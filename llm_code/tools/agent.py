"""AgentTool — spawns a sub-agent runtime to handle a delegated sub-task."""
from __future__ import annotations

import asyncio
import concurrent.futures
import inspect
from typing import Callable

from pydantic import BaseModel

from llm_code.api.types import StreamTextDelta
from llm_code.tools.agent_roles import BUILT_IN_ROLES, AgentRole
from llm_code.tools.base import PermissionLevel, Tool, ToolResult


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
        return "Spawn a sub-agent to handle a sub-task"

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "Task for the sub-agent",
                },
                "model": {
                    "type": "string",
                    "description": "Optional model override",
                },
                "role": {
                    "type": "string",
                    "enum": ["explore", "plan", "verify"],
                    "description": "Built-in agent role with restricted tools",
                },
            },
            "required": ["task"],
        }

    @property
    def required_permission(self) -> PermissionLevel:
        return PermissionLevel.FULL_ACCESS

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

        task: str = args.get("task", "")
        model: str | None = args.get("model") or None
        role_name: str = args.get("role", "")

        # Resolve role
        role: AgentRole | None = None
        if role_name:
            role = BUILT_IN_ROLES.get(role_name)
            if role is None:
                return ToolResult(
                    output=f"Unknown role: '{role_name}'. Valid roles: {list(BUILT_IN_ROLES)}",
                    is_error=True,
                )

        # Sync wrapper: works whether or not an event loop is running
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            with concurrent.futures.ThreadPoolExecutor() as pool:
                result = pool.submit(
                    asyncio.run, self._execute_async(task, model, role)
                ).result()
        else:
            result = asyncio.run(self._execute_async(task, model, role))

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
