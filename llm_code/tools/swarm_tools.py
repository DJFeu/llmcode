"""Consolidated swarm tools — create, delete, list, message.

All swarm tool classes live here. The original per-file modules
(swarm_create, swarm_delete, swarm_list, swarm_message) re-export
from this file for backward compatibility.
"""
from __future__ import annotations

import asyncio

from pydantic import BaseModel

from llm_code.swarm.manager import SwarmManager
from llm_code.tools.base import PermissionLevel, Tool, ToolResult


# ---------------------------------------------------------------------------
# SwarmCreate
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# SwarmDelete
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# SwarmList
# ---------------------------------------------------------------------------

class SwarmListTool(Tool):
    def __init__(self, manager: SwarmManager) -> None:
        self._manager = manager

    @property
    def name(self) -> str:
        return "swarm_list"

    @property
    def description(self) -> str:
        return "List all active swarm worker agents with their roles, tasks, and status."

    @property
    def input_schema(self) -> dict:
        return {"type": "object", "properties": {}}

    @property
    def required_permission(self) -> PermissionLevel:
        return PermissionLevel.READ_ONLY

    def is_read_only(self, args: dict) -> bool:
        return True

    def is_concurrency_safe(self, args: dict) -> bool:
        return True

    def execute(self, args: dict) -> ToolResult:
        members = self._manager.list_members()
        if not members:
            return ToolResult(output="No swarm members active.")
        lines = []
        for m in members:
            lines.append(
                f"- {m.id} | role={m.role} | task={m.task[:50]} | "
                f"backend={m.backend} | pid={m.pid} | status={m.status.value}"
            )
        return ToolResult(output="\n".join(lines))


# ---------------------------------------------------------------------------
# SwarmMessage
# ---------------------------------------------------------------------------

class SwarmMessageInput(BaseModel):
    action: str       # "send" | "receive" | "broadcast" | "pending"
    from_id: str = ""
    to_id: str = ""
    text: str = ""
    member_ids: list[str] = []


class SwarmMessageTool(Tool):
    def __init__(self, manager: SwarmManager) -> None:
        self._manager = manager

    @property
    def name(self) -> str:
        return "swarm_message"

    @property
    def description(self) -> str:
        return (
            "Send and receive messages between swarm members. "
            "Actions: 'send' (from_id, to_id, text), "
            "'receive' (from_id, to_id), "
            "'broadcast' (from_id, member_ids, text), "
            "'pending' (to_id — show unread messages for a member)."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["send", "receive", "broadcast", "pending"],
                    "description": "Message action to perform",
                },
                "from_id": {"type": "string", "description": "Sender ID"},
                "to_id": {"type": "string", "description": "Receiver ID"},
                "text": {"type": "string", "description": "Message text"},
                "member_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of member IDs for broadcast",
                },
            },
            "required": ["action"],
        }

    @property
    def required_permission(self) -> PermissionLevel:
        return PermissionLevel.READ_ONLY

    @property
    def input_model(self) -> type[SwarmMessageInput]:
        return SwarmMessageInput

    def execute(self, args: dict) -> ToolResult:
        action = args["action"]
        mailbox = self._manager.mailbox

        if action == "send":
            from_id = args.get("from_id", "main")
            to_id = args.get("to_id", "")
            text = args.get("text", "")
            if not to_id or not text:
                return ToolResult(output="send requires to_id and text", is_error=True)
            mailbox.send(from_id, to_id, text)
            return ToolResult(output=f"Message sent from {from_id} to {to_id}")

        if action == "receive":
            from_id = args.get("from_id", "")
            to_id = args.get("to_id", "")
            if not from_id or not to_id:
                return ToolResult(output="receive requires from_id and to_id", is_error=True)
            msgs = mailbox.receive(from_id, to_id)
            if not msgs:
                return ToolResult(output="No messages.")
            lines = [f"[{m.timestamp}] {m.from_id} -> {m.to_id}: {m.text}" for m in msgs]
            return ToolResult(output="\n".join(lines))

        if action == "broadcast":
            from_id = args.get("from_id", "main")
            member_ids = args.get("member_ids", [])
            text = args.get("text", "")
            if not member_ids or not text:
                return ToolResult(output="broadcast requires member_ids and text", is_error=True)
            mailbox.broadcast(from_id, member_ids, text)
            return ToolResult(output=f"Broadcast sent to {len(member_ids)} members")

        if action == "pending":
            to_id = args.get("to_id", "")
            if not to_id:
                return ToolResult(output="pending requires to_id", is_error=True)
            msgs = mailbox.pending_for(to_id)
            if not msgs:
                return ToolResult(output=f"No pending messages for {to_id}")
            lines = [f"[{m.timestamp}] {m.from_id}: {m.text}" for m in msgs]
            return ToolResult(output="\n".join(lines))

        return ToolResult(output=f"Unknown action: {action}", is_error=True)
