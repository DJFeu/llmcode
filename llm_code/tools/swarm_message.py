"""SwarmMessageTool — send and receive messages between swarm members."""
from __future__ import annotations

from pydantic import BaseModel

from llm_code.swarm.manager import SwarmManager
from llm_code.tools.base import PermissionLevel, Tool, ToolResult


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
