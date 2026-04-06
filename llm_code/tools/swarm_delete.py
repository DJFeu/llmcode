"""SwarmDeleteTool — stop one or all swarm members."""
from __future__ import annotations

import asyncio

from pydantic import BaseModel

from llm_code.swarm.manager import SwarmManager
from llm_code.tools.base import PermissionLevel, Tool, ToolResult


class SwarmDeleteInput(BaseModel):
    action: str = "stop"        # "stop" | "stop_all"
    member_id: str = ""


class SwarmDeleteTool(Tool):
    def __init__(self, manager: SwarmManager) -> None:
        self._manager = manager

    @property
    def name(self) -> str:
        return "swarm_delete"

    @property
    def description(self) -> str:
        return (
            "Stop one or all swarm members. "
            "action='stop' + member_id to stop a single member. "
            "action='stop_all' to stop all members."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["stop", "stop_all"],
                    "description": "stop (one) or stop_all",
                    "default": "stop",
                },
                "member_id": {
                    "type": "string",
                    "description": "ID of the member to stop (for action=stop)",
                },
            },
            "required": ["action"],
        }

    @property
    def required_permission(self) -> PermissionLevel:
        return PermissionLevel.WORKSPACE_WRITE

    @property
    def input_model(self) -> type[SwarmDeleteInput]:
        return SwarmDeleteInput

    def execute(self, args: dict) -> ToolResult:
        action = args.get("action", "stop")

        try:
            running = False
            try:
                asyncio.get_running_loop()
                running = True
            except RuntimeError:
                pass

            if action == "stop_all":
                if running:
                    import concurrent.futures
                    with concurrent.futures.ThreadPoolExecutor() as pool:
                        pool.submit(asyncio.run, self._manager.stop_all()).result()
                else:
                    asyncio.run(self._manager.stop_all())
                return ToolResult(output="All swarm members stopped.")

            member_id = args.get("member_id", "")
            if not member_id:
                return ToolResult(output="member_id is required for action=stop", is_error=True)

            if running:
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    pool.submit(asyncio.run, self._manager.stop_member(member_id)).result()
            else:
                asyncio.run(self._manager.stop_member(member_id))
            return ToolResult(output=f"Stopped swarm member {member_id}")

        except KeyError:
            return ToolResult(output=f"No swarm member with id '{args.get('member_id')}'", is_error=True)
        except Exception as exc:
            return ToolResult(output=f"Error: {exc}", is_error=True)
