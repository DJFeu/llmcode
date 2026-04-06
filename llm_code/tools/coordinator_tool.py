"""CoordinatorTool — auto-decompose and delegate a task to swarm workers."""
from __future__ import annotations

import asyncio
import concurrent.futures

from pydantic import BaseModel

from llm_code.tools.base import PermissionLevel, Tool, ToolResult


class CoordinatorInput(BaseModel):
    task: str


class CoordinatorTool(Tool):
    """Tool that auto-decomposes a high-level task and dispatches to swarm workers."""

    def __init__(self, coordinator: object) -> None:
        self._coordinator = coordinator

    @property
    def name(self) -> str:
        return "coordinate"

    @property
    def description(self) -> str:
        return (
            "Auto-decompose a high-level task into subtasks and delegate each one "
            "to a separate swarm worker agent. The coordinator monitors progress "
            "and returns an aggregated summary when all workers finish (or timeout)."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "High-level task to decompose and delegate to worker agents.",
                },
            },
            "required": ["task"],
        }

    @property
    def required_permission(self) -> PermissionLevel:
        return PermissionLevel.WORKSPACE_WRITE

    @property
    def input_model(self) -> type[CoordinatorInput]:
        return CoordinatorInput

    def execute(self, args: dict) -> ToolResult:
        task = args["task"]
        try:
            try:
                asyncio.get_running_loop()
                running = True
            except RuntimeError:
                running = False

            if running:
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    result = pool.submit(
                        asyncio.run,
                        self._coordinator.orchestrate(task),
                    ).result()
            else:
                result = asyncio.run(self._coordinator.orchestrate(task))

            return ToolResult(output=result)
        except Exception as exc:
            return ToolResult(output=f"Coordinator error: {exc}", is_error=True)
