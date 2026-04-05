"""Tests for audit logging system."""
from __future__ import annotations

import json

import pytest

from llm_code.enterprise.audit import (
    AuditEvent,
    CompositeAuditLogger,
    FileAuditLogger,
)


class TestAuditEvent:
    def test_create_minimal(self) -> None:
        event = AuditEvent(timestamp="2026-04-05T12:00:00Z", event_type="tool_execute", user_id="local")
        assert event.tool_name == ""
        assert event.outcome == ""
        assert event.metadata == {}

    def test_create_full(self) -> None:
        event = AuditEvent(
            timestamp="2026-04-05T12:00:00Z", event_type="tool_execute", user_id="u1",
            tool_name="bash", action="ls -la", outcome="allowed", metadata={"cwd": "/tmp"},
        )
        assert event.tool_name == "bash"
        assert event.outcome == "allowed"


class TestFileAuditLogger:
    @pytest.mark.asyncio
    async def test_log_creates_file(self, tmp_path) -> None:
        logger = FileAuditLogger(audit_dir=tmp_path)
        event = AuditEvent(timestamp="2026-04-05T12:00:00Z", event_type="test", user_id="local")
        await logger.log(event)
        files = list(tmp_path.glob("*.jsonl"))
        assert len(files) == 1
        line = files[0].read_text().strip()
        data = json.loads(line)
        assert data["event_type"] == "test"

    @pytest.mark.asyncio
    async def test_log_appends(self, tmp_path) -> None:
        logger = FileAuditLogger(audit_dir=tmp_path)
        for i in range(3):
            event = AuditEvent(timestamp=f"2026-04-05T12:0{i}:00Z", event_type=f"event_{i}", user_id="local")
            await logger.log(event)
        files = list(tmp_path.glob("*.jsonl"))
        lines = files[0].read_text().strip().split("\n")
        assert len(lines) == 3

    @pytest.mark.asyncio
    async def test_daily_file_naming(self, tmp_path) -> None:
        logger = FileAuditLogger(audit_dir=tmp_path)
        event = AuditEvent(timestamp="2026-04-05T12:00:00Z", event_type="test", user_id="local")
        await logger.log(event)
        assert (tmp_path / "2026-04-05.jsonl").exists()


class TestCompositeAuditLogger:
    @pytest.mark.asyncio
    async def test_logs_to_multiple(self, tmp_path) -> None:
        dir1 = tmp_path / "a"
        dir2 = tmp_path / "b"
        logger = CompositeAuditLogger(loggers=[
            FileAuditLogger(audit_dir=dir1),
            FileAuditLogger(audit_dir=dir2),
        ])
        event = AuditEvent(timestamp="2026-04-05T12:00:00Z", event_type="test", user_id="local")
        await logger.log(event)
        assert len(list(dir1.glob("*.jsonl"))) == 1
        assert len(list(dir2.glob("*.jsonl"))) == 1
