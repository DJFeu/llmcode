"""M7 — in-memory inter-agent mailbox."""
from __future__ import annotations

from llm_code.swarm.inter_agent_mailbox import Mailbox, InterAgentMessage


class TestSendReceive:
    def test_send_returns_sequenced_message(self) -> None:
        mb = Mailbox()
        msg = mb.send("main", "worker-1", "task", {"goal": "lint"})
        assert isinstance(msg, InterAgentMessage)
        assert msg.seq == 1
        assert msg.sender == "main"
        assert msg.receiver == "worker-1"
        assert msg.kind == "task"
        assert msg.payload == {"goal": "lint"}

    def test_seq_monotonically_increases_across_receivers(self) -> None:
        mb = Mailbox()
        a = mb.send("main", "a", "ping")
        b = mb.send("main", "b", "ping")
        c = mb.send("main", "a", "ping")
        assert (a.seq, b.seq, c.seq) == (1, 2, 3)

    def test_payload_is_copied_not_shared(self) -> None:
        """Caller mutations to the input dict must not corrupt the queued message."""
        mb = Mailbox()
        payload = {"count": 1}
        mb.send("main", "worker", "task", payload)
        payload["count"] = 999
        received = mb.poll("worker")
        assert received is not None
        assert received.payload == {"count": 1}


class TestPollDrain:
    def test_poll_is_fifo(self) -> None:
        mb = Mailbox()
        for i in range(3):
            mb.send("main", "w", "ping", {"i": i})
        order = [mb.poll("w").payload["i"] for _ in range(3)]
        assert order == [0, 1, 2]

    def test_poll_empty_returns_none(self) -> None:
        mb = Mailbox()
        assert mb.poll("nobody") is None

    def test_drain_returns_all_in_fifo_order(self) -> None:
        mb = Mailbox()
        for i in range(5):
            mb.send("main", "w", "ping", {"i": i})
        drained = mb.drain("w")
        assert [m.payload["i"] for m in drained] == [0, 1, 2, 3, 4]
        assert mb.pending("w") == 0

    def test_pending_counts_queue_depth(self) -> None:
        mb = Mailbox()
        mb.send("main", "w", "ping")
        mb.send("main", "w", "ping")
        assert mb.pending("w") == 2
        mb.poll("w")
        assert mb.pending("w") == 1
