"""Integration tests for v5: memory, indexer, and prompt integration."""
from __future__ import annotations

from pathlib import Path


from llm_code.runtime.memory import MemoryStore
from llm_code.runtime.indexer import ProjectIndexer
from llm_code.runtime.prompt import SystemPromptBuilder
from llm_code.runtime.context import ProjectContext
from llm_code.tools.memory_tools import MemoryStoreTool, MemoryRecallTool, MemoryListTool
from llm_code.tools.registry import ToolRegistry


def test_memory_roundtrip(tmp_path):
    store = MemoryStore(tmp_path / "mem", Path("/project/a"))
    store.store("arch", "We use microservices")
    store.save_session_summary("Built the auth module")

    # Simulate new session
    store2 = MemoryStore(tmp_path / "mem", Path("/project/a"))
    assert store2.recall("arch") == "We use microservices"
    summaries = store2.load_recent_summaries()
    assert len(summaries) == 1
    assert "auth" in summaries[0]


def test_memory_isolation(tmp_path):
    store_a = MemoryStore(tmp_path / "mem", Path("/project/a"))
    store_b = MemoryStore(tmp_path / "mem", Path("/project/b"))
    store_a.store("key", "value_a")
    store_b.store("key", "value_b")
    assert store_a.recall("key") == "value_a"
    assert store_b.recall("key") == "value_b"


def test_index_in_prompt(tmp_path):
    (tmp_path / "app.py").write_text("class App:\n    pass\n\ndef main():\n    pass\n")
    indexer = ProjectIndexer(tmp_path)
    index = indexer.build_index()

    builder = SystemPromptBuilder()
    ctx = ProjectContext(cwd=tmp_path, is_git_repo=False, git_status="", instructions="")
    prompt = builder.build(ctx, project_index=index)
    assert "App" in prompt
    assert "main" in prompt
    assert "Project Index" in prompt


def test_memory_in_prompt(tmp_path):
    builder = SystemPromptBuilder()
    ctx = ProjectContext(cwd=tmp_path, is_git_repo=False, git_status="", instructions="")
    prompt = builder.build(
        ctx,
        memory_entries={"arch": "microservices", "db": "PostgreSQL"},
        memory_summaries=["Built auth module", "Fixed login bug"],
    )
    assert "microservices" in prompt
    assert "Project Memory" in prompt
    assert "Recent Sessions" in prompt
    assert "auth module" in prompt


def test_memory_tools_in_registry(tmp_path):
    store = MemoryStore(tmp_path / "mem", Path("/project"))
    registry = ToolRegistry()
    registry.register(MemoryStoreTool(store))
    registry.register(MemoryRecallTool(store))
    registry.register(MemoryListTool(store))
    assert registry.get("memory_store") is not None
    assert registry.get("memory_recall") is not None
    assert registry.get("memory_list") is not None

    # Store and recall via registry
    registry.execute("memory_store", {"key": "test", "value": "hello"})
    result = registry.execute("memory_recall", {"key": "test"})
    assert result.output == "hello"
