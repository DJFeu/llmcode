"""Tests for swarm file-based mailbox."""
from __future__ import annotations


import pytest

from llm_code.swarm.mailbox import Mailbox
from llm_code.swarm.types import SwarmMessage


@pytest.fixture
def mailbox(tmp_path):
    return Mailbox(tmp_path / "swarm" / "mailbox")


class TestMailboxSend:
    def test_send_creates_file(self, mailbox, tmp_path):
        mailbox.send("main", "worker-1", "hello")
        files = list((tmp_path / "swarm" / "mailbox").glob("main_to_worker-1.jsonl"))
        assert len(files) == 1

    def test_send_appends_jsonl(self, mailbox):
        mailbox.send("main", "worker-1", "msg1")
        mailbox.send("main", "worker-1", "msg2")
        messages = mailbox.receive("main", "worker-1")
        assert len(messages) == 2
        assert messages[0].text == "msg1"
        assert messages[1].text == "msg2"

    def test_send_sets_timestamp(self, mailbox):
        mailbox.send("a", "b", "test")
        msgs = mailbox.receive("a", "b")
        assert msgs[0].timestamp  # non-empty ISO string


class TestMailboxReceive:
    def test_receive_empty(self, mailbox):
        msgs = mailbox.receive("x", "y")
        assert msgs == []

    def test_receive_returns_swarm_messages(self, mailbox):
        mailbox.send("main", "w1", "do this")
        msgs = mailbox.receive("main", "w1")
        assert isinstance(msgs[0], SwarmMessage)
        assert msgs[0].from_id == "main"
        assert msgs[0].to_id == "w1"

    def test_receive_and_clear(self, mailbox):
        mailbox.send("a", "b", "msg")
        mailbox.receive_and_clear("a", "b")
        assert mailbox.receive("a", "b") == []


class TestMailboxBroadcast:
    def test_broadcast_sends_to_all(self, mailbox):
        member_ids = ["w1", "w2", "w3"]
        mailbox.broadcast("main", member_ids, "all stop")
        for mid in member_ids:
            msgs = mailbox.receive("main", mid)
            assert len(msgs) == 1
            assert msgs[0].text == "all stop"


class TestMailboxPending:
    def test_pending_for_returns_unread(self, mailbox):
        mailbox.send("main", "w1", "task1")
        mailbox.send("w2", "w1", "update")
        pending = mailbox.pending_for("w1")
        assert len(pending) == 2

    def test_pending_empty(self, mailbox):
        assert mailbox.pending_for("nobody") == []
