"""Audit logging — JSONL file logger with composite support."""
from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class AuditEvent:
    timestamp: str
    event_type: str
    user_id: str
    tool_name: str = ""
    action: str = ""
    outcome: str = ""
    metadata: dict = field(default_factory=dict)


class AuditLogger(ABC):
    @abstractmethod
    async def log(self, event: AuditEvent) -> None: ...


class FileAuditLogger(AuditLogger):
    def __init__(self, audit_dir: Path) -> None:
        self._audit_dir = audit_dir

    async def log(self, event: AuditEvent) -> None:
        self._audit_dir.mkdir(parents=True, exist_ok=True)
        date_str = event.timestamp[:10]
        path = self._audit_dir / f"{date_str}.jsonl"
        line = json.dumps({
            "timestamp": event.timestamp,
            "event_type": event.event_type,
            "user_id": event.user_id,
            "tool_name": event.tool_name,
            "action": event.action,
            "outcome": event.outcome,
            "metadata": event.metadata,
        })
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")


class CompositeAuditLogger(AuditLogger):
    def __init__(self, loggers: list[AuditLogger]) -> None:
        self._loggers = loggers

    async def log(self, event: AuditEvent) -> None:
        for logger in self._loggers:
            try:
                await logger.log(event)
            except Exception as exc:
                _log.warning("Audit logger failed: %s", exc)
