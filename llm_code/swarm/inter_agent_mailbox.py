"""Inter-agent message mailbox (M7).

Per-receiver FIFO queue with a global monotonic sequence. Lets swarm
coordinators ship structured messages between agents without grabbing
onto each other's internals. Distinct from :mod:`llm_code.swarm.mailbox`
(the file-based JSONL variant used by the existing swarm CLI) — this is
an in-memory, non-persistent sibling aimed at short-lived, test-friendly
coordination loops.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from itertools import count
from threading import Lock


@dataclass(frozen=True)
class InterAgentMessage:
    seq: int
    sender: str
    receiver: str
    kind: str
    payload: dict = field(default_factory=dict)


class Mailbox:
    """In-memory, per-receiver FIFO queue."""

    def __init__(self) -> None:
        self._queues: dict[str, deque[InterAgentMessage]] = {}
        self._seq = count(1)
        self._lock = Lock()

    def send(
        self,
        sender: str,
        receiver: str,
        kind: str,
        payload: dict | None = None,
    ) -> InterAgentMessage:
        msg = InterAgentMessage(
            seq=next(self._seq),
            sender=sender,
            receiver=receiver,
            kind=kind,
            payload=dict(payload or {}),
        )
        with self._lock:
            self._queues.setdefault(receiver, deque()).append(msg)
        return msg

    def poll(self, receiver: str) -> InterAgentMessage | None:
        """Pop the oldest message for ``receiver`` (FIFO), or ``None``."""
        with self._lock:
            q = self._queues.get(receiver)
            if not q:
                return None
            return q.popleft()

    def drain(self, receiver: str) -> list[InterAgentMessage]:
        """Pop all messages for ``receiver`` in FIFO order."""
        with self._lock:
            q = self._queues.pop(receiver, None)
            return list(q) if q else []

    def pending(self, receiver: str) -> int:
        with self._lock:
            q = self._queues.get(receiver)
            return len(q) if q else 0
