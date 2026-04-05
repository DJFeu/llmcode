"""Remote execution protocol — JSON-RPC over WebSocket."""
from __future__ import annotations
from dataclasses import dataclass
import json

# Server → Client events (same as Ink IPC protocol)
# Client → Server commands

@dataclass
class RemoteMessage:
    """Base message format for client-server communication."""
    type: str
    data: dict

    def to_json(self) -> str:
        return json.dumps({"type": self.type, **self.data})

    @classmethod
    def from_json(cls, text: str) -> RemoteMessage:
        obj = json.loads(text)
        msg_type = obj.pop("type", "unknown")
        return cls(type=msg_type, data=obj)
