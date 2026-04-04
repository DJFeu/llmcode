"""File-based JSONL mailbox for inter-agent communication."""
from __future__ import annotations

import fcntl
import json
from datetime import datetime, timezone
from pathlib import Path

from llm_code.swarm.types import SwarmMessage


class Mailbox:
    """JSONL-based message passing between swarm members.

    Messages stored at: <base_dir>/<sender>_to_<receiver>.jsonl
    Uses file locking to prevent concurrent write corruption.
    """

    def __init__(self, base_dir: Path) -> None:
        self._dir = Path(base_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

    def send(self, from_id: str, to_id: str, text: str) -> SwarmMessage:
        """Append a message to the sender->receiver JSONL file (with file lock)."""
        ts = datetime.now(timezone.utc).isoformat()
        msg = SwarmMessage(from_id=from_id, to_id=to_id, text=text, timestamp=ts)
        path = self._msg_path(from_id, to_id)
        with open(path, "a", encoding="utf-8") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                f.write(json.dumps({
                    "from_id": msg.from_id,
                    "to_id": msg.to_id,
                    "text": msg.text,
                    "timestamp": msg.timestamp,
                }) + "\n")
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
        return msg

    def receive(self, from_id: str, to_id: str) -> list[SwarmMessage]:
        """Read all messages from sender->receiver."""
        path = self._msg_path(from_id, to_id)
        if not path.exists():
            return []
        messages: list[SwarmMessage] = []
        for line in path.read_text(encoding="utf-8").strip().splitlines():
            if not line:
                continue
            data = json.loads(line)
            messages.append(SwarmMessage(
                from_id=data["from_id"],
                to_id=data["to_id"],
                text=data["text"],
                timestamp=data["timestamp"],
            ))
        return messages

    def receive_and_clear(self, from_id: str, to_id: str) -> list[SwarmMessage]:
        """Read all messages then delete the file."""
        msgs = self.receive(from_id, to_id)
        path = self._msg_path(from_id, to_id)
        if path.exists():
            path.unlink()
        return msgs

    def broadcast(self, from_id: str, to_ids: list[str], text: str) -> list[SwarmMessage]:
        """Send the same message to multiple receivers."""
        return [self.send(from_id, to_id, text) for to_id in to_ids]

    def pending_for(self, to_id: str) -> list[SwarmMessage]:
        """Return all unread messages addressed to a given member."""
        messages: list[SwarmMessage] = []
        for path in self._dir.glob(f"*_to_{to_id}.jsonl"):
            for line in path.read_text(encoding="utf-8").strip().splitlines():
                if not line:
                    continue
                data = json.loads(line)
                messages.append(SwarmMessage(
                    from_id=data["from_id"],
                    to_id=data["to_id"],
                    text=data["text"],
                    timestamp=data["timestamp"],
                ))
        return messages

    def _msg_path(self, from_id: str, to_id: str) -> Path:
        return self._dir / f"{from_id}_to_{to_id}.jsonl"
