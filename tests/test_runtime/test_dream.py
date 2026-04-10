"""Tests for DreamTask — background memory consolidation."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from llm_code.api.types import Message, MessageResponse, TextBlock, TokenUsage
from llm_code.runtime.config import DreamConfig, RuntimeConfig
from llm_code.runtime.dream import DreamTask
from llm_code.runtime.memory import MemoryStore
from llm_code.runtime.session import Session


def _make_session(num_messages: int, project_path: Path) -> Session:
    """Create a session with the given number of user/assistant message pairs."""
    session = Session.create(project_path)
    for i in range(num_messages):
        role = "user" if i % 2 == 0 else "assistant"
        msg = Message(role=role, content=(TextBlock(text=f"message {i}"),))
        session = session.add_message(msg)
    return session


def _seed_trigger_guards(store: MemoryStore) -> None:
    """Pre-seed the memory store so DreamTask trigger guards pass."""
    # Set session count >= 5
    store.store("_dream_session_count", "5")
    # Set last run to > 24h ago (or leave absent — no prior run means pass)


def _make_provider(response_text: str = "# Summary\nModified: foo.py") -> AsyncMock:
    """Create a mock provider that returns a canned response."""
    provider = AsyncMock()
    provider.send_message.return_value = MessageResponse(
        content=(TextBlock(text=response_text),),
        usage=TokenUsage(input_tokens=100, output_tokens=50),
        stop_reason="end_turn",
    )
    return provider


class TestDreamTask:
    @pytest.mark.asyncio
    async def test_consolidate_returns_summary(self, tmp_path):
        store = MemoryStore(tmp_path / "mem", Path("/project/a"))
        _seed_trigger_guards(store)
        session = _make_session(6, Path("/project/a"))
        provider = _make_provider("# Summary\nModified: foo.py")
        config = RuntimeConfig()

        task = DreamTask()
        result = await task.consolidate(session, store, provider, config)

        assert "Summary" in result
        assert "foo.py" in result

    @pytest.mark.asyncio
    async def test_consolidate_writes_file_to_consolidated_dir(self, tmp_path):
        store = MemoryStore(tmp_path / "mem", Path("/project/a"))
        _seed_trigger_guards(store)
        session = _make_session(6, Path("/project/a"))
        provider = _make_provider("# Consolidated\nDecision: use dataclasses")
        config = RuntimeConfig()

        task = DreamTask()
        await task.consolidate(session, store, provider, config)

        files = list(store.consolidated_dir.glob("*.md"))
        assert len(files) == 1
        content = files[0].read_text()
        assert "Consolidated" in content

    @pytest.mark.asyncio
    async def test_consolidate_skips_when_too_few_messages(self, tmp_path):
        store = MemoryStore(tmp_path / "mem", Path("/project/a"))
        session = _make_session(2, Path("/project/a"))  # only 2 < min_turns=3
        provider = _make_provider()
        config = RuntimeConfig()

        task = DreamTask()
        result = await task.consolidate(session, store, provider, config)

        assert result == ""
        provider.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_consolidate_skips_when_disabled(self, tmp_path):
        store = MemoryStore(tmp_path / "mem", Path("/project/a"))
        session = _make_session(10, Path("/project/a"))
        provider = _make_provider()
        config = RuntimeConfig(dream=DreamConfig(enabled=False))

        task = DreamTask()
        result = await task.consolidate(session, store, provider, config)

        assert result == ""
        provider.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_consolidate_updates_dream_last_run(self, tmp_path):
        store = MemoryStore(tmp_path / "mem", Path("/project/a"))
        _seed_trigger_guards(store)
        session = _make_session(6, Path("/project/a"))
        provider = _make_provider("summary")
        config = RuntimeConfig()

        task = DreamTask()
        await task.consolidate(session, store, provider, config)

        last_run = store.recall("_dream_last_run")
        assert last_run is not None
        # Should be a valid ISO timestamp
        datetime.fromisoformat(last_run)

    @pytest.mark.asyncio
    async def test_consolidate_builds_correct_prompt(self, tmp_path):
        store = MemoryStore(tmp_path / "mem", Path("/project/a"))
        _seed_trigger_guards(store)
        session = _make_session(6, Path("/project/a"))
        provider = _make_provider("summary")
        config = RuntimeConfig()

        task = DreamTask()
        await task.consolidate(session, store, provider, config)

        call_args = provider.send_message.call_args
        request = call_args[0][0]
        # System prompt should mention consolidation
        assert "consolidat" in request.system.lower() or "summar" in request.system.lower()
        # Messages should contain session content
        assert len(request.messages) >= 1

    @pytest.mark.asyncio
    async def test_consolidate_handles_provider_error_gracefully(self, tmp_path):
        store = MemoryStore(tmp_path / "mem", Path("/project/a"))
        _seed_trigger_guards(store)
        session = _make_session(6, Path("/project/a"))
        provider = AsyncMock()
        provider.send_message.side_effect = RuntimeError("API down")
        config = RuntimeConfig()

        task = DreamTask()
        result = await task.consolidate(session, store, provider, config)

        assert result == ""
        # No file should be written
        files = list(store.consolidated_dir.glob("*.md"))
        assert len(files) == 0

    @pytest.mark.asyncio
    async def test_consolidate_uses_file_lock(self, tmp_path):
        store = MemoryStore(tmp_path / "mem", Path("/project/a"))
        _seed_trigger_guards(store)
        session = _make_session(6, Path("/project/a"))
        provider = _make_provider("locked summary")
        config = RuntimeConfig()

        task = DreamTask()
        with patch("llm_code.runtime.dream.FileLock") as mock_lock:
            mock_lock.return_value.__enter__ = MagicMock(return_value=None)
            mock_lock.return_value.__exit__ = MagicMock(return_value=False)
            await task.consolidate(session, store, provider, config)
            mock_lock.assert_called_once()

    @pytest.mark.asyncio
    async def test_consolidate_counts_user_messages_not_total(self, tmp_path):
        """min_turns counts user messages, not total messages."""
        store = MemoryStore(tmp_path / "mem", Path("/project/a"))
        # 4 total messages but only 2 user messages (< min_turns=3)
        session = Session.create(Path("/project/a"))
        for role in ("user", "assistant", "user", "assistant"):
            msg = Message(role=role, content=(TextBlock(text=f"msg from {role}"),))
            session = session.add_message(msg)

        provider = _make_provider()
        config = RuntimeConfig(dream=DreamConfig(min_turns=3))

        task = DreamTask()
        result = await task.consolidate(session, store, provider, config)

        assert result == ""
        provider.send_message.assert_not_called()


class TestDreamTaskWithLayeredMemory:
    @pytest.mark.asyncio
    async def test_dream_writes_via_project_memory(self, tmp_path):
        """DreamTask should work with ProjectMemory's underlying MemoryStore."""
        from llm_code.runtime.memory_layers import ProjectMemory

        pm = ProjectMemory(memory_dir=tmp_path / "mem", project_path=Path("/proj"))
        _seed_trigger_guards(pm.memory_store)
        session = _make_session(6, Path("/proj"))
        provider = _make_provider("# Dream summary")
        config = RuntimeConfig()

        task = DreamTask()
        # DreamTask accepts MemoryStore — ProjectMemory exposes it
        result = await task.consolidate(
            session, pm.memory_store, provider, config
        )
        assert "Dream summary" in result
        # Consolidated file exists
        files = list(pm.memory_store.consolidated_dir.glob("*.md"))
        assert len(files) == 1

    @pytest.mark.asyncio
    async def test_dream_last_run_readable_from_project_memory(self, tmp_path):
        """_dream_last_run stored by DreamTask is accessible via ProjectMemory."""
        from llm_code.runtime.memory_layers import ProjectMemory

        pm = ProjectMemory(memory_dir=tmp_path / "mem", project_path=Path("/proj"))
        _seed_trigger_guards(pm.memory_store)
        session = _make_session(6, Path("/proj"))
        provider = _make_provider("summary")
        config = RuntimeConfig()

        task = DreamTask()
        await task.consolidate(session, pm.memory_store, provider, config)

        entry = pm.recall("_dream_last_run")
        assert entry is not None
        assert entry.value  # non-empty ISO timestamp


class TestDreamSlashCommands:
    """Tests for /memory consolidate and /memory history integration points."""

    @pytest.mark.asyncio
    async def test_consolidate_command_calls_dream_task(self, tmp_path):
        """Verify the consolidate subcommand invokes DreamTask."""
        store = MemoryStore(tmp_path / "mem", Path("/project/a"))
        _seed_trigger_guards(store)
        session = _make_session(6, Path("/project/a"))
        provider = _make_provider("# Manual consolidation")

        task = DreamTask()
        result = await task.consolidate(
            session, store, provider, RuntimeConfig()
        )
        assert "Manual consolidation" in result

    def test_history_returns_past_consolidations(self, tmp_path):
        """Verify load_consolidated_summaries returns stored summaries."""
        store = MemoryStore(tmp_path / "mem", Path("/project/a"))
        store.save_consolidated("# Day 1 summary", date_str="2026-04-01")
        store.save_consolidated("# Day 2 summary", date_str="2026-04-02")
        summaries = store.load_consolidated_summaries(limit=10)
        assert len(summaries) == 2
        assert "Day 2" in summaries[0]  # newest first


class TestDreamEdgeCases:
    @pytest.mark.asyncio
    async def test_consolidate_appends_to_existing_date(self, tmp_path):
        """If consolidated for today already exists, overwrite with latest."""
        store = MemoryStore(tmp_path / "mem", Path("/project/a"))
        _seed_trigger_guards(store)
        store.save_consolidated("old summary", date_str="2026-04-03")
        session = _make_session(6, Path("/project/a"))
        provider = _make_provider("new summary")
        config = RuntimeConfig()

        task = DreamTask()
        await task.consolidate(session, store, provider, config)

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        files = list(store.consolidated_dir.glob(f"{today}.md"))
        assert len(files) == 1
        assert "new summary" in files[0].read_text()

    @pytest.mark.asyncio
    async def test_consolidate_with_empty_session(self, tmp_path):
        store = MemoryStore(tmp_path / "mem", Path("/project/a"))
        session = Session.create(Path("/project/a"))  # no messages
        provider = _make_provider()
        config = RuntimeConfig()

        task = DreamTask()
        result = await task.consolidate(session, store, provider, config)
        assert result == ""

    @pytest.mark.asyncio
    async def test_consolidate_with_tool_result_blocks(self, tmp_path):
        """Session containing ToolResultBlocks should not crash."""
        from llm_code.api.types import ToolResultBlock, ToolUseBlock

        store = MemoryStore(tmp_path / "mem", Path("/project/a"))
        _seed_trigger_guards(store)
        session = Session.create(Path("/project/a"))
        # Add user message
        session = session.add_message(
            Message(role="user", content=(TextBlock(text="Fix the bug"),))
        )
        # Add assistant with tool use
        session = session.add_message(
            Message(role="assistant", content=(
                TextBlock(text="I'll read the file"),
                ToolUseBlock(id="t1", name="read_file", input={"path": "foo.py"}),
            ))
        )
        # Add tool result
        session = session.add_message(
            Message(role="user", content=(
                ToolResultBlock(tool_use_id="t1", content="file contents here", is_error=False),
            ))
        )
        # Add more user turns to meet min_turns
        for i in range(3):
            session = session.add_message(
                Message(role="user", content=(TextBlock(text=f"follow up {i}"),))
            )
            session = session.add_message(
                Message(role="assistant", content=(TextBlock(text=f"response {i}"),))
            )

        provider = _make_provider("# Summary with tools")
        config = RuntimeConfig()

        task = DreamTask()
        result = await task.consolidate(session, store, provider, config)
        assert "Summary with tools" in result

    @pytest.mark.asyncio
    async def test_concurrent_consolidation_is_safe(self, tmp_path):
        """Two concurrent consolidations should not corrupt the file."""
        store = MemoryStore(tmp_path / "mem", Path("/project/a"))
        _seed_trigger_guards(store)
        session = _make_session(6, Path("/project/a"))
        provider = _make_provider("concurrent result")
        config = RuntimeConfig()

        task = DreamTask()
        results = await asyncio.gather(
            task.consolidate(session, store, provider, config),
            task.consolidate(session, store, provider, config),
        )
        # At least one should succeed; the other may be skipped because the
        # first consolidation resets _dream_session_count to "0".
        successful = [r for r in results if r == "concurrent result"]
        assert len(successful) >= 1
        files = list(store.consolidated_dir.glob("*.md"))
        assert len(files) == 1


class TestDreamTriggerGuards:
    @pytest.mark.asyncio
    async def test_skips_when_last_run_under_24h(self, tmp_path):
        """Should skip if last consolidation was less than 24h ago."""
        store = MemoryStore(tmp_path / "mem", Path("/project/a"))
        store.store("_dream_session_count", "10")
        # Set last run to 1 hour ago
        from datetime import timedelta
        recent = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        store.store("_dream_last_run", recent)

        session = _make_session(6, Path("/project/a"))
        provider = _make_provider()
        config = RuntimeConfig()

        task = DreamTask()
        result = await task.consolidate(session, store, provider, config)
        assert result == ""
        provider.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_session_count_under_5(self, tmp_path):
        """Should skip if fewer than 5 sessions since last consolidation."""
        store = MemoryStore(tmp_path / "mem", Path("/project/a"))
        store.store("_dream_session_count", "3")

        session = _make_session(6, Path("/project/a"))
        provider = _make_provider()
        config = RuntimeConfig()

        task = DreamTask()
        result = await task.consolidate(session, store, provider, config)
        assert result == ""
        provider.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_proceeds_when_last_run_over_24h(self, tmp_path):
        """Should proceed if last consolidation was more than 24h ago."""
        from datetime import timedelta
        store = MemoryStore(tmp_path / "mem", Path("/project/a"))
        store.store("_dream_session_count", "5")
        old = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        store.store("_dream_last_run", old)

        session = _make_session(6, Path("/project/a"))
        provider = _make_provider("ok")
        config = RuntimeConfig()

        task = DreamTask()
        result = await task.consolidate(session, store, provider, config)
        assert result != ""

    @pytest.mark.asyncio
    async def test_resets_session_count_after_consolidation(self, tmp_path):
        """Session count should be reset to 0 after successful consolidation."""
        store = MemoryStore(tmp_path / "mem", Path("/project/a"))
        _seed_trigger_guards(store)
        session = _make_session(6, Path("/project/a"))
        provider = _make_provider("done")
        config = RuntimeConfig()

        task = DreamTask()
        await task.consolidate(session, store, provider, config)

        assert store.recall("_dream_session_count") == "0"


class TestDreamDateNormalization:
    def test_normalize_today(self):
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        result = DreamTask._normalize_dates("Modified today in the session")
        assert today_str in result
        assert "today" not in result.lower()

    def test_normalize_yesterday(self):
        from datetime import timedelta
        yesterday_str = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        result = DreamTask._normalize_dates("Fixed yesterday")
        assert yesterday_str in result

    def test_normalize_days_ago(self):
        from datetime import timedelta
        three_days_ago = (datetime.now(timezone.utc) - timedelta(days=3)).strftime("%Y-%m-%d")
        result = DreamTask._normalize_dates("Created 3 days ago")
        assert three_days_ago in result

    def test_no_change_when_no_relative_dates(self):
        text = "Modified on 2026-01-15"
        assert DreamTask._normalize_dates(text) == text


class TestDreamSessionCounter:
    def test_increment_from_zero(self, tmp_path):
        store = MemoryStore(tmp_path / "mem", Path("/project/a"))
        DreamTask.increment_session_count(store)
        assert store.recall("_dream_session_count") == "1"

    def test_increment_existing(self, tmp_path):
        store = MemoryStore(tmp_path / "mem", Path("/project/a"))
        store.store("_dream_session_count", "4")
        DreamTask.increment_session_count(store)
        assert store.recall("_dream_session_count") == "5"


class TestDreamPruneMemoryIndex:
    def test_prune_by_line_count(self, tmp_path):
        store = MemoryStore(tmp_path / "mem", Path("/project/a"))
        # Create MEMORY.md in the parent directory of store._dir
        index_path = store._dir.parent / "MEMORY.md"
        index_path.write_text("\n".join(f"line {i}" for i in range(250)), encoding="utf-8")

        DreamTask._prune_memory_index(store)

        content = index_path.read_text(encoding="utf-8")
        assert len(content.splitlines()) <= 200

    def test_prune_by_byte_size(self, tmp_path):
        store = MemoryStore(tmp_path / "mem", Path("/project/a"))
        index_path = store._dir.parent / "MEMORY.md"
        # Write > 25KB of content (short lines to stay under 200 lines initially)
        big_line = "x" * 200 + "\n"
        index_path.write_text(big_line * 150, encoding="utf-8")  # ~30KB, 150 lines

        DreamTask._prune_memory_index(store)

        content = index_path.read_text(encoding="utf-8")
        assert len(content.encode("utf-8")) <= 25 * 1024

    def test_no_change_when_within_limits(self, tmp_path):
        store = MemoryStore(tmp_path / "mem", Path("/project/a"))
        index_path = store._dir.parent / "MEMORY.md"
        original = "# Memory\n- item 1\n- item 2\n"
        index_path.write_text(original, encoding="utf-8")

        DreamTask._prune_memory_index(store)

        assert index_path.read_text(encoding="utf-8") == original

    def test_no_crash_when_no_memory_file(self, tmp_path):
        store = MemoryStore(tmp_path / "mem", Path("/project/a"))
        # No MEMORY.md — should not raise
        DreamTask._prune_memory_index(store)
