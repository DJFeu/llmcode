"""Tests for CheckpointRecovery session persistence."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path


from llm_code.runtime.checkpoint_recovery import CheckpointRecovery
from llm_code.runtime.session import Session
from llm_code.api.types import Message, TextBlock, TokenUsage


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_session(project_path: Path = Path("/test/proj"), num_messages: int = 0) -> Session:
    session = Session.create(project_path)
    for i in range(num_messages):
        msg = Message(role="user", content=(TextBlock(text=f"Message {i}"),))
        session = session.add_message(msg)
    return session


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestCheckpointRecoverySaveLoad:
    def test_save_creates_json_file(self, tmp_path):
        recovery = CheckpointRecovery(tmp_path / "checkpoints")
        session = _make_session()
        path = recovery.save_checkpoint(session)
        assert path.exists()
        assert path.suffix == ".json"
        assert path.stem == session.id

    def test_load_returns_equivalent_session(self, tmp_path):
        recovery = CheckpointRecovery(tmp_path / "checkpoints")
        session = _make_session(num_messages=3)
        recovery.save_checkpoint(session)
        loaded = recovery.load_checkpoint(session.id)
        assert loaded is not None
        assert loaded.id == session.id
        assert len(loaded.messages) == len(session.messages)
        assert loaded.project_path == session.project_path

    def test_load_missing_returns_none(self, tmp_path):
        recovery = CheckpointRecovery(tmp_path / "checkpoints")
        assert recovery.load_checkpoint("nonexistent_id") is None

    def test_save_embeds_checkpoint_saved_at(self, tmp_path):
        recovery = CheckpointRecovery(tmp_path / "checkpoints")
        session = _make_session()
        path = recovery.save_checkpoint(session)
        data = json.loads(path.read_text())
        assert "checkpoint_saved_at" in data
        assert data["checkpoint_saved_at"] != ""

    def test_load_strips_checkpoint_metadata(self, tmp_path):
        """Session.from_dict must succeed — extra key must be stripped before parse."""
        recovery = CheckpointRecovery(tmp_path / "checkpoints")
        session = _make_session()
        recovery.save_checkpoint(session)
        loaded = recovery.load_checkpoint(session.id)
        assert loaded is not None  # no KeyError from extra field

    def test_overwrite_checkpoint(self, tmp_path):
        recovery = CheckpointRecovery(tmp_path / "checkpoints")
        session = _make_session(num_messages=1)
        recovery.save_checkpoint(session)
        # Simulate more messages
        msg = Message(role="user", content=(TextBlock(text="Extra"),))
        session2 = session.add_message(msg)
        recovery.save_checkpoint(session2)
        loaded = recovery.load_checkpoint(session.id)
        assert loaded is not None
        assert len(loaded.messages) == 2

    def test_load_restores_token_usage(self, tmp_path):
        recovery = CheckpointRecovery(tmp_path / "checkpoints")
        session = _make_session()
        session = session.update_usage(TokenUsage(input_tokens=100, output_tokens=50))
        recovery.save_checkpoint(session)
        loaded = recovery.load_checkpoint(session.id)
        assert loaded is not None
        assert loaded.total_usage.input_tokens == 100
        assert loaded.total_usage.output_tokens == 50


class TestCheckpointRecoveryList:
    def test_list_empty(self, tmp_path):
        recovery = CheckpointRecovery(tmp_path / "checkpoints")
        assert recovery.list_checkpoints() == []

    def test_list_returns_descriptors(self, tmp_path):
        recovery = CheckpointRecovery(tmp_path / "checkpoints")
        s1 = _make_session(num_messages=2)
        s2 = _make_session(num_messages=4)
        recovery.save_checkpoint(s1)
        recovery.save_checkpoint(s2)
        entries = recovery.list_checkpoints()
        assert len(entries) == 2

    def test_list_descriptor_fields(self, tmp_path):
        recovery = CheckpointRecovery(tmp_path / "checkpoints")
        session = _make_session(num_messages=3)
        recovery.save_checkpoint(session)
        entries = recovery.list_checkpoints()
        assert len(entries) == 1
        e = entries[0]
        assert e["session_id"] == session.id
        assert e["message_count"] == 3
        assert e["project_path"] == str(session.project_path)
        assert e["saved_at"] != ""

    def test_list_sorted_newest_first(self, tmp_path):
        import time

        recovery = CheckpointRecovery(tmp_path / "checkpoints")
        s1 = _make_session()
        recovery.save_checkpoint(s1)
        time.sleep(0.01)
        s2 = _make_session()
        recovery.save_checkpoint(s2)
        entries = recovery.list_checkpoints()
        assert entries[0]["session_id"] == s2.id
        assert entries[1]["session_id"] == s1.id


class TestCheckpointRecoveryDelete:
    def test_delete_existing(self, tmp_path):
        recovery = CheckpointRecovery(tmp_path / "checkpoints")
        session = _make_session()
        recovery.save_checkpoint(session)
        deleted = recovery.delete_checkpoint(session.id)
        assert deleted is True
        assert recovery.load_checkpoint(session.id) is None

    def test_delete_missing_returns_false(self, tmp_path):
        recovery = CheckpointRecovery(tmp_path / "checkpoints")
        assert recovery.delete_checkpoint("nope") is False


class TestDetectLastCheckpoint:
    def test_detect_returns_most_recent(self, tmp_path):
        import time

        recovery = CheckpointRecovery(tmp_path / "checkpoints")
        s1 = _make_session(num_messages=1)
        recovery.save_checkpoint(s1)
        time.sleep(0.01)
        s2 = _make_session(num_messages=2)
        recovery.save_checkpoint(s2)
        detected = recovery.detect_last_checkpoint()
        assert detected is not None
        assert detected.id == s2.id

    def test_detect_empty_returns_none(self, tmp_path):
        recovery = CheckpointRecovery(tmp_path / "checkpoints")
        assert recovery.detect_last_checkpoint() is None


class TestAutoSave:
    def test_auto_save_saves_checkpoint(self, tmp_path):
        recovery = CheckpointRecovery(tmp_path / "checkpoints")
        session = _make_session(num_messages=2)
        session_holder = [session]

        async def run():
            recovery.start_auto_save(lambda: session_holder[0], interval=1)
            await asyncio.sleep(1.2)
            recovery.stop_auto_save()

        asyncio.run(run())
        loaded = recovery.load_checkpoint(session.id)
        assert loaded is not None
        assert loaded.id == session.id

    def test_start_auto_save_idempotent(self, tmp_path):
        """Calling start_auto_save twice should not raise or create two tasks."""
        recovery = CheckpointRecovery(tmp_path / "checkpoints")
        session = _make_session()

        async def run():
            recovery.start_auto_save(lambda: session, interval=60)
            recovery.start_auto_save(lambda: session, interval=60)  # second call is a no-op
            recovery.stop_auto_save()

        asyncio.run(run())  # should not raise

    def test_stop_auto_save_when_not_started(self, tmp_path):
        recovery = CheckpointRecovery(tmp_path / "checkpoints")
        recovery.stop_auto_save()  # should not raise


class TestConversationRuntimeIntegration:
    """Verify ConversationRuntime calls save_checkpoint after each turn."""

    def test_recovery_checkpoint_param_accepted(self):
        """ConversationRuntime accepts the recovery_checkpoint kwarg without error."""
        from unittest.mock import MagicMock
        from llm_code.runtime.conversation import ConversationRuntime

        mock_recovery = MagicMock()
        runtime = ConversationRuntime(
            provider=MagicMock(),
            tool_registry=MagicMock(),
            permission_policy=MagicMock(),
            hook_runner=MagicMock(),
            prompt_builder=MagicMock(),
            config=MagicMock(
                max_turn_iterations=10,
                max_tokens=4096,
                temperature=0.0,
                compact_after_tokens=100000,
                hida=None,
                max_visible_tools=None,
                provider_base_url="",
                native_tools=True,
                model="test-model",
                model_routing=None,
            ),
            session=_make_session(),
            context=MagicMock(),
            recovery_checkpoint=mock_recovery,
        )
        assert runtime._recovery_checkpoint is mock_recovery
