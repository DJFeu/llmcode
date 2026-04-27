"""Tests for agent-memory subagent wiring (v16 M2)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from llm_code.runtime.agent_memory import AgentMemoryStore, AgentMemoryView
from llm_code.runtime.subagent_factory import (
    _agent_memory_enabled,
    _ensure_agent_memory_store,
)
from llm_code.tools.agent_memory_tools import (
    MemoryListTool,
    MemoryReadTool,
    MemoryWriteTool,
    build_memory_tools,
)


# ---------------------------------------------------------------------------
# AgentMemoryStore + AgentMemoryView
# ---------------------------------------------------------------------------


class TestAgentMemoryStore:
    def test_view_creates_per_agent_cell(self) -> None:
        store = AgentMemoryStore()
        a = store.view("agent-A")
        b = store.view("agent-B")
        a.write("k", "value-A")
        b.write("k", "value-B")
        assert a.read("k") == "value-A"
        assert b.read("k") == "value-B"

    def test_same_id_shares_state(self) -> None:
        store = AgentMemoryStore()
        first = store.view("worker")
        first.write("step", "1")
        second = store.view("worker")
        assert second.read("step") == "1"

    def test_view_rejects_empty_id(self) -> None:
        store = AgentMemoryStore()
        with pytest.raises(ValueError, match="non-empty"):
            store.view("")


class TestAgentMemoryView:
    def test_read_missing_returns_none(self) -> None:
        view = AgentMemoryStore().view("a")
        assert view.read("absent") is None

    def test_write_then_list(self) -> None:
        view = AgentMemoryStore().view("a")
        view.write("k1", "v1")
        view.write("k2", "v2")
        assert view.list_keys() == ("k1", "k2")

    def test_delete(self) -> None:
        view = AgentMemoryStore().view("a")
        view.write("ephemeral", "x")
        assert view.delete("ephemeral") is True
        assert view.read("ephemeral") is None
        assert view.delete("ephemeral") is False

    def test_write_rejects_empty_key(self) -> None:
        view = AgentMemoryStore().view("a")
        with pytest.raises(ValueError, match="non-empty"):
            view.write("", "v")

    def test_write_rejects_oversized_value(self) -> None:
        view = AgentMemoryStore().view("a")
        oversized = "x" * (65536 + 1)
        with pytest.raises(ValueError, match="64 KiB"):
            view.write("k", oversized)

    def test_view_constructor_rejects_empty_id(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            AgentMemoryView("", AgentMemoryStore())


# ---------------------------------------------------------------------------
# Tool wrappers
# ---------------------------------------------------------------------------


class TestMemoryReadTool:
    def test_read_existing(self) -> None:
        view = AgentMemoryStore().view("a")
        view.write("greeting", "hello")
        tool = MemoryReadTool(view)
        result = tool.execute({"key": "greeting"})
        assert result.is_error is False
        assert result.output == "hello"

    def test_read_missing(self) -> None:
        tool = MemoryReadTool(AgentMemoryStore().view("a"))
        result = tool.execute({"key": "absent"})
        assert result.is_error is False
        assert "not found" in result.output

    def test_read_empty_key(self) -> None:
        tool = MemoryReadTool(AgentMemoryStore().view("a"))
        result = tool.execute({"key": ""})
        assert result.is_error is True


class TestMemoryWriteTool:
    def test_write(self) -> None:
        view = AgentMemoryStore().view("a")
        tool = MemoryWriteTool(view)
        result = tool.execute({"key": "k", "value": "v"})
        assert result.is_error is False
        assert view.read("k") == "v"

    def test_write_rejects_non_string_value(self) -> None:
        tool = MemoryWriteTool(AgentMemoryStore().view("a"))
        result = tool.execute({"key": "k", "value": 42})
        assert result.is_error is True

    def test_write_oversized_returns_error(self) -> None:
        tool = MemoryWriteTool(AgentMemoryStore().view("a"))
        result = tool.execute({"key": "k", "value": "x" * 70000})
        assert result.is_error is True
        assert "64 KiB" in result.output


class TestMemoryListTool:
    def test_list_empty(self) -> None:
        tool = MemoryListTool(AgentMemoryStore().view("a"))
        result = tool.execute({})
        assert result.output == "(empty)"

    def test_list_sorted(self) -> None:
        view = AgentMemoryStore().view("a")
        view.write("zeta", "z")
        view.write("alpha", "a")
        result = MemoryListTool(view).execute({})
        assert result.output.splitlines() == ["alpha", "zeta"]


# ---------------------------------------------------------------------------
# build_memory_tools + subagent_factory helpers
# ---------------------------------------------------------------------------


class TestBuildMemoryTools:
    def test_returns_three_tools(self) -> None:
        tools = build_memory_tools(AgentMemoryStore().view("a"))
        names = [t.name for t in tools]
        assert names == ["memory_read", "memory_write", "memory_list"]


class TestAgentMemoryEnabledHelpers:
    def test_default_when_no_profile(self) -> None:
        parent = SimpleNamespace(_config=None)
        assert _agent_memory_enabled(parent) is True

    def test_profile_flag_off(self) -> None:
        cfg = SimpleNamespace(
            profile=SimpleNamespace(agent_memory_enabled=False),
        )
        parent = SimpleNamespace(_config=cfg)
        assert _agent_memory_enabled(parent) is False

    def test_flat_config_flag(self) -> None:
        cfg = SimpleNamespace(profile=None, agent_memory_enabled=False)
        parent = SimpleNamespace(_config=cfg)
        assert _agent_memory_enabled(parent) is False

    def test_ensure_store_creates_once(self) -> None:
        parent = SimpleNamespace()
        store = _ensure_agent_memory_store(parent)
        again = _ensure_agent_memory_store(parent)
        assert store is again
        assert isinstance(store, AgentMemoryStore)


# ---------------------------------------------------------------------------
# Cross-spawn persistence — view-level integration
# ---------------------------------------------------------------------------


class TestCrossSpawnPersistence:
    def test_two_views_same_id_share_state(self) -> None:
        store = AgentMemoryStore()
        first_spawn = store.view("researcher")
        first_spawn.write("draft", "v1")

        second_spawn = store.view("researcher")
        assert second_spawn.read("draft") == "v1"

    def test_isolation_between_ids(self) -> None:
        store = AgentMemoryStore()
        store.view("agent-A").write("secret", "a")
        store.view("agent-B").write("secret", "b")
        assert store.view("agent-A").read("secret") == "a"
        assert store.view("agent-B").read("secret") == "b"
