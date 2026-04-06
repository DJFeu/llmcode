"""SwarmCreateTool — spawn a new swarm worker agent."""
from __future__ import annotations

import asyncio

from pydantic import BaseModel

from llm_code.swarm.manager import SwarmManager
from llm_code.tools.base import PermissionLevel, Tool, ToolResult


class SwarmCreateInput(BaseModel):
    role: str
    task: str
    backend: str = "auto"
    model: str | None = None


class SwarmCreateTool(Tool):
    def __init__(self, manager: SwarmManager) -> None:
        self._manager = manager

    @property
    def name(self) -> str:
        return "swarm_create"

    @property
    def description(self) -> str:
        return (
            "Spawn a new swarm worker agent with a given role and task. "
            "The worker runs as a separate llm-code --lite process. "
            "Backend: 'auto' (tmux if available, else subprocess), 'tmux', or 'subprocess'."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "role": {
                    "type": "string",
                    "description": "Role of the worker (e.g. 'security reviewer', 'test writer')",
                },
                "task": {
                    "type": "string",
                    "description": "Task description for the worker to execute",
                },
                "backend": {
                    "type": "string",
                    "enum": ["auto", "tmux", "subprocess"],
                    "description": "Backend to use (default: auto)",
                    "default": "auto",
                },
                "model": {
                    "type": "string",
                    "description": (
                        "Override the LLM model for this specific swarm member. "
                        "When omitted, the model is resolved via the config fallback chain: "
                        "role_models -> model_routing.sub_agent -> global model."
                    ),
                },
            },
            "required": ["role", "task"],
        }

    @property
    def required_permission(self) -> PermissionLevel:
        return PermissionLevel.WORKSPACE_WRITE

    @property
    def input_model(self) -> type[SwarmCreateInput]:
        return SwarmCreateInput

    def execute(self, args: dict) -> ToolResult:
        role = args["role"]
        task = args["task"]
        backend = args.get("backend", "auto")
        model = args.get("model")

        try:
            try:
                asyncio.get_running_loop()
                running = True
            except RuntimeError:
                running = False

            if running:
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    member = pool.submit(
                        asyncio.run,
                        self._manager.create_member(role=role, task=task, backend=backend, model=model),
                    ).result()
            else:
                member = asyncio.run(
                    self._manager.create_member(role=role, task=task, backend=backend, model=model)
                )
        except ValueError as exc:
            return ToolResult(output=str(exc), is_error=True)

        return ToolResult(
            output=(
                f"Created swarm member {member.id}\n"
                f"  Role: {member.role}\n"
                f"  Task: {member.task}\n"
                f"  Backend: {member.backend}\n"
                f"  PID: {member.pid}"
            )
        )
