"""Tests for llm_code.tools.memory_tools — TDD (RED first)."""
from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from llm_code.runtime.memory import MemoryStore
from llm_code.tools.base import PermissionLevel
from llm_code.tools.memory_tools import MemoryListTool, MemoryRecallTool, MemoryStoreTool


@pytest.fixture()
def mem_store(tmp_path) -> MemoryStore:
    return MemoryStore(tmp_path / "mem", Path("/project/test"))


class TestMemoryStoreTool:
    def test_name(self, mem_store):
        assert MemoryStoreTool(mem_store).name == "memory_store"

    def test_permission(self, mem_store):
        assert MemoryStoreTool(mem_store).required_permission == PermissionLevel.WORKSPACE_WRITE

    def test_execute_stores_and_returns_confirmation(self, mem_store):
        tool = MemoryStoreTool(mem_store)
        result = tool.execute({"key": "lang", "value": "Python"})
        assert result.is_error is False
        assert "lang" in result.output
        assert mem_store.recall("lang") == "Python"

    def test_execute_overwrites_existing_key(self, mem_store):
        tool = MemoryStoreTool(mem_store)
        tool.execute({"key": "lang", "value": "Python"})
        tool.execute({"key": "lang", "value": "Go"})
        assert mem_store.recall("lang") == "Go"

    def test_validate_input_rejects_missing_key(self, mem_store):
        tool = MemoryStoreTool(mem_store)
        with pytest.raises(ValidationError):
            tool.validate_input({"value": "Python"})

    def test_validate_input_rejects_missing_value(self, mem_store):
        tool = MemoryStoreTool(mem_store)
        with pytest.raises(ValidationError):
            tool.validate_input({"key": "lang"})

    def test_has_input_schema(self, mem_store):
        schema = MemoryStoreTool(mem_store).input_schema
        assert "key" in schema["properties"]
        assert "value" in schema["properties"]

    def test_not_read_only(self, mem_store):
        tool = MemoryStoreTool(mem_store)
        assert tool.is_read_only({}) is False


class TestMemoryRecallTool:
    def test_name(self, mem_store):
        assert MemoryRecallTool(mem_store).name == "memory_recall"

    def test_permission(self, mem_store):
        assert MemoryRecallTool(mem_store).required_permission == PermissionLevel.READ_ONLY

    def test_execute_returns_stored_value(self, mem_store):
        mem_store.store("project", "llm-code")
        tool = MemoryRecallTool(mem_store)
        result = tool.execute({"key": "project"})
        assert result.is_error is False
        assert result.output == "llm-code"

    def test_execute_missing_key_returns_error(self, mem_store):
        tool = MemoryRecallTool(mem_store)
        result = tool.execute({"key": "missing"})
        assert result.is_error is True
        assert "missing" in result.output

    def test_validate_input_rejects_missing_key(self, mem_store):
        tool = MemoryRecallTool(mem_store)
        with pytest.raises(ValidationError):
            tool.validate_input({})

    def test_is_read_only(self, mem_store):
        tool = MemoryRecallTool(mem_store)
        assert tool.is_read_only({}) is True

    def test_is_concurrency_safe(self, mem_store):
        tool = MemoryRecallTool(mem_store)
        assert tool.is_concurrency_safe({}) is True

    def test_has_input_schema(self, mem_store):
        schema = MemoryRecallTool(mem_store).input_schema
        assert "key" in schema["properties"]


class TestMemoryListTool:
    def test_name(self, mem_store):
        assert MemoryListTool(mem_store).name == "memory_list"

    def test_permission(self, mem_store):
        assert MemoryListTool(mem_store).required_permission == PermissionLevel.READ_ONLY

    def test_execute_shows_all_entries(self, mem_store):
        mem_store.store("lang", "Python")
        mem_store.store("project", "llm-code")
        tool = MemoryListTool(mem_store)
        result = tool.execute({})
        assert result.is_error is False
        assert "lang" in result.output
        assert "project" in result.output

    def test_execute_empty_store(self, mem_store):
        tool = MemoryListTool(mem_store)
        result = tool.execute({})
        assert result.is_error is False
        assert "No memories" in result.output

    def test_long_value_truncated(self, mem_store):
        long_val = "x" * 100
        mem_store.store("bigkey", long_val)
        tool = MemoryListTool(mem_store)
        result = tool.execute({})
        # Should show truncated value with ellipsis
        assert "..." in result.output
        # Should not show the full 100-char value
        assert long_val not in result.output

    def test_short_value_not_truncated(self, mem_store):
        mem_store.store("short", "hi")
        tool = MemoryListTool(mem_store)
        result = tool.execute({})
        assert "hi" in result.output

    def test_is_read_only(self, mem_store):
        tool = MemoryListTool(mem_store)
        assert tool.is_read_only({}) is True

    def test_has_input_schema(self, mem_store):
        schema = MemoryListTool(mem_store).input_schema
        assert schema["type"] == "object"
