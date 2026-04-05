"""Frozen dataclasses for swarm member state and messages."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class SwarmStatus(Enum):
    STARTING = "starting"
    RUNNING = "running"
    STOPPED = "stopped"
    FAILED = "failed"


@dataclass(frozen=True)
class SwarmMember:
    id: str
    role: str
    task: str
    backend: str          # "tmux" | "subprocess"
    pid: int | None
    status: SwarmStatus
    model: str = ""


@dataclass(frozen=True)
class SwarmMessage:
    from_id: str
    to_id: str            # member id, or "*" for broadcast
    text: str
    timestamp: str        # ISO-8601
