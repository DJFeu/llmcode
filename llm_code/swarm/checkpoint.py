"""Checkpoint system for agent teams — save/restore agent state for resume."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class AgentCheckpoint:
    member_id: str
    role: str
    status: str
    conversation_snapshot: tuple[dict, ...]
    last_tool_call: str | None = None
    output: str = ""


@dataclass(frozen=True)
class TeamCheckpoint:
    team_name: str
    task_description: str
    timestamp: str
    checkpoints: tuple[AgentCheckpoint, ...]
    coordinator_state: dict = field(default_factory=dict)
    completed_members: tuple[str, ...] = ()


def save_checkpoint(checkpoint: TeamCheckpoint, checkpoints_dir: Path) -> Path:
    checkpoints_dir.mkdir(parents=True, exist_ok=True)
    safe_ts = checkpoint.timestamp.replace(":", "-")
    filename = f"{checkpoint.team_name}-{safe_ts}.json"
    path = checkpoints_dir / filename
    data = {
        "team_name": checkpoint.team_name,
        "task_description": checkpoint.task_description,
        "timestamp": checkpoint.timestamp,
        "checkpoints": [
            {
                "member_id": cp.member_id, "role": cp.role, "status": cp.status,
                "conversation_snapshot": list(cp.conversation_snapshot),
                "last_tool_call": cp.last_tool_call, "output": cp.output,
            }
            for cp in checkpoint.checkpoints
        ],
        "coordinator_state": checkpoint.coordinator_state,
        "completed_members": list(checkpoint.completed_members),
    }
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


def load_checkpoint(path: Path) -> TeamCheckpoint:
    data = json.loads(path.read_text(encoding="utf-8"))
    checkpoints = tuple(
        AgentCheckpoint(
            member_id=cp["member_id"], role=cp["role"], status=cp["status"],
            conversation_snapshot=tuple(cp.get("conversation_snapshot", [])),
            last_tool_call=cp.get("last_tool_call"), output=cp.get("output", ""),
        )
        for cp in data.get("checkpoints", [])
    )
    return TeamCheckpoint(
        team_name=data["team_name"], task_description=data.get("task_description", ""),
        timestamp=data.get("timestamp", ""), checkpoints=checkpoints,
        coordinator_state=data.get("coordinator_state", {}),
        completed_members=tuple(data.get("completed_members", [])),
    )


def list_checkpoints(checkpoints_dir: Path) -> list[Path]:
    if not checkpoints_dir.is_dir():
        return []
    return sorted(checkpoints_dir.glob("*.json"))
