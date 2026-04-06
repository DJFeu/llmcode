"""Tests for multi-layer memory dataclasses and components."""
from __future__ import annotations

import dataclasses
from datetime import datetime, timezone
from pathlib import Path

import pytest

from llm_code.runtime.memory_layers import (
    GovernanceLayer,
    GovernanceRule,
    LayeredMemory,
    MemoryEntry,
    ProjectMemory,
    TaskMemory,
    TaskRecord,
    WorkingMemory,
)


class TestGovernanceRule:
    def test_defaults(self):
        rule = GovernanceRule(
            category="style",
            content="Use type annotations",
            source="CLAUDE.md",
        )
        assert rule.category == "style"
        assert rule.content == "Use type annotations"
        assert rule.source == "CLAUDE.md"
        assert rule.priority == 0

    def test_frozen(self):
        rule = GovernanceRule(
            category="style", content="x", source="y"
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            rule.category = "other"  # type: ignore[misc]

    def test_custom_priority(self):
        rule = GovernanceRule(
            category="security",
            content="No hardcoded secrets",
            source=".llmcode/rules/security.md",
            priority=10,
        )
        assert rule.priority == 10


class TestMemoryEntry:
    def test_defaults(self):
        now = datetime.now(timezone.utc).isoformat()
        entry = MemoryEntry(
            key="project_lang",
            value="Python 3.11",
            tags=("config", "language"),
            created_at=now,
            accessed_at=now,
        )
        assert entry.key == "project_lang"
        assert entry.tags == ("config", "language")

    def test_frozen(self):
        now = datetime.now(timezone.utc).isoformat()
        entry = MemoryEntry(
            key="k", value="v", tags=(), created_at=now, accessed_at=now,
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            entry.value = "new"  # type: ignore[misc]

    def test_empty_tags(self):
        now = datetime.now(timezone.utc).isoformat()
        entry = MemoryEntry(
            key="k", value="v", tags=(), created_at=now, accessed_at=now,
        )
        assert entry.tags == ()


class TestTaskRecord:
    def test_defaults(self):
        now = datetime.now(timezone.utc).isoformat()
        task = TaskRecord(
            task_id="abc123",
            description="Implement feature X",
            status="incomplete",
            created_at=now,
            updated_at=now,
        )
        assert task.status == "incomplete"
        assert task.metadata == {}

    def test_frozen(self):
        now = datetime.now(timezone.utc).isoformat()
        task = TaskRecord(
            task_id="t1",
            description="d",
            status="incomplete",
            created_at=now,
            updated_at=now,
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            task.status = "complete"  # type: ignore[misc]

    def test_custom_metadata(self):
        now = datetime.now(timezone.utc).isoformat()
        task = TaskRecord(
            task_id="t1",
            description="d",
            status="incomplete",
            created_at=now,
            updated_at=now,
            metadata={"priority": "high", "files": ["a.py"]},
        )
        assert task.metadata["priority"] == "high"


class TestGovernanceLayer:
    def test_scan_claude_md(self, tmp_path):
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text("# Rules\n\n- Always use type hints\n- No print statements\n")
        layer = GovernanceLayer(project_root=tmp_path)
        rules = layer.scan()
        assert len(rules) >= 1
        assert any("type hints" in r.content for r in rules)
        assert all(r.source.endswith("CLAUDE.md") for r in rules)

    def test_scan_rules_directory(self, tmp_path):
        rules_dir = tmp_path / ".llmcode" / "rules"
        rules_dir.mkdir(parents=True)
        (rules_dir / "style.md").write_text("# Style\n\n- Use black formatter\n- Max line length 100\n")
        (rules_dir / "security.md").write_text("# Security\n\n- No hardcoded secrets\n")
        layer = GovernanceLayer(project_root=tmp_path)
        rules = layer.scan()
        assert len(rules) >= 2
        sources = {r.source for r in rules}
        assert any("style.md" in s for s in sources)
        assert any("security.md" in s for s in sources)

    def test_scan_governance_md(self, tmp_path):
        gov_md = tmp_path / ".llmcode" / "governance.md"
        gov_md.parent.mkdir(parents=True)
        gov_md.write_text("# Governance\n\n- All PRs require review\n")
        layer = GovernanceLayer(project_root=tmp_path)
        rules = layer.scan()
        assert any("PRs require review" in r.content for r in rules)

    def test_scan_missing_files_returns_empty(self, tmp_path):
        layer = GovernanceLayer(project_root=tmp_path)
        rules = layer.scan()
        assert rules == ()

    def test_scan_assigns_priority_by_source(self, tmp_path):
        """governance.md rules get higher priority than CLAUDE.md."""
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text("- Rule from CLAUDE.md\n")
        gov_md = tmp_path / ".llmcode" / "governance.md"
        gov_md.parent.mkdir(parents=True)
        gov_md.write_text("- Rule from governance.md\n")
        layer = GovernanceLayer(project_root=tmp_path)
        rules = layer.scan()
        gov_rules = [r for r in rules if "governance.md" in r.source]
        claude_rules = [r for r in rules if "CLAUDE.md" in r.source]
        assert all(g.priority >= c.priority for g in gov_rules for c in claude_rules)

    def test_rules_are_frozen_tuples(self, tmp_path):
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text("- A rule\n")
        layer = GovernanceLayer(project_root=tmp_path)
        rules = layer.scan()
        assert isinstance(rules, tuple)


class TestWorkingMemory:
    def test_store_and_recall(self):
        wm = WorkingMemory()
        wm.store("key1", "value1")
        assert wm.recall("key1") == "value1"

    def test_recall_missing_returns_none(self):
        wm = WorkingMemory()
        assert wm.recall("nope") is None

    def test_delete(self):
        wm = WorkingMemory()
        wm.store("k", "v")
        wm.delete("k")
        assert wm.recall("k") is None

    def test_list_keys(self):
        wm = WorkingMemory()
        wm.store("a", "1")
        wm.store("b", "2")
        assert set(wm.list_keys()) == {"a", "b"}

    def test_get_all_returns_dict(self):
        wm = WorkingMemory()
        wm.store("x", "y")
        result = wm.get_all()
        assert "x" in result
        assert result["x"] == "y"

    def test_clear(self):
        wm = WorkingMemory()
        wm.store("a", "1")
        wm.clear()
        assert wm.list_keys() == []

    def test_not_persisted(self):
        """Working memory is session-scoped; new instance is empty."""
        wm1 = WorkingMemory()
        wm1.store("k", "v")
        wm2 = WorkingMemory()
        assert wm2.recall("k") is None


class TestProjectMemory:
    def test_store_and_recall(self, tmp_path):
        pm = ProjectMemory(memory_dir=tmp_path / "mem", project_path=Path("/proj"))
        pm.store("lang", "Python", tags=("config",))
        entry = pm.recall("lang")
        assert entry is not None
        assert entry.value == "Python"
        assert "config" in entry.tags

    def test_recall_missing_returns_none(self, tmp_path):
        pm = ProjectMemory(memory_dir=tmp_path / "mem", project_path=Path("/proj"))
        assert pm.recall("nope") is None

    def test_query_by_tag(self, tmp_path):
        pm = ProjectMemory(memory_dir=tmp_path / "mem", project_path=Path("/proj"))
        pm.store("lang", "Python", tags=("config", "setup"))
        pm.store("framework", "FastAPI", tags=("config", "web"))
        pm.store("note", "Remember to test", tags=("reminder",))
        results = pm.query_by_tag("config")
        assert len(results) == 2
        keys = {e.key for e in results}
        assert keys == {"lang", "framework"}

    def test_delete(self, tmp_path):
        pm = ProjectMemory(memory_dir=tmp_path / "mem", project_path=Path("/proj"))
        pm.store("k", "v")
        pm.delete("k")
        assert pm.recall("k") is None

    def test_list_keys(self, tmp_path):
        pm = ProjectMemory(memory_dir=tmp_path / "mem", project_path=Path("/proj"))
        pm.store("a", "1")
        pm.store("b", "2")
        assert set(pm.list_keys()) == {"a", "b"}

    def test_get_all_returns_memory_entries(self, tmp_path):
        pm = ProjectMemory(memory_dir=tmp_path / "mem", project_path=Path("/proj"))
        pm.store("x", "y", tags=("t1",))
        result = pm.get_all()
        assert "x" in result
        assert isinstance(result["x"], MemoryEntry)
        assert result["x"].tags == ("t1",)

    def test_persists_across_instances(self, tmp_path):
        pm1 = ProjectMemory(memory_dir=tmp_path / "mem", project_path=Path("/proj"))
        pm1.store("k", "v", tags=("a",))
        pm2 = ProjectMemory(memory_dir=tmp_path / "mem", project_path=Path("/proj"))
        entry = pm2.recall("k")
        assert entry is not None
        assert entry.value == "v"
        assert entry.tags == ("a",)

    def test_updates_accessed_at_on_recall(self, tmp_path):
        pm = ProjectMemory(memory_dir=tmp_path / "mem", project_path=Path("/proj"))
        pm.store("k", "v")
        entry1 = pm.recall("k")
        # Re-recall to trigger accessed_at update
        entry2 = pm.recall("k")
        assert entry2 is not None
        assert entry2.accessed_at >= entry1.accessed_at

    def test_wraps_legacy_memory_store(self, tmp_path):
        """ProjectMemory delegates persistence to MemoryStore."""
        pm = ProjectMemory(memory_dir=tmp_path / "mem", project_path=Path("/proj"))
        pm.store("k", "v")
        # Verify the underlying MemoryStore file exists
        assert pm.memory_store._memory_file.exists()


class TestTaskMemory:
    def test_create_task(self, tmp_path):
        tm = TaskMemory(memory_dir=tmp_path / "mem", project_path=Path("/proj"))
        task = tm.create("Implement feature X")
        assert task.status == "incomplete"
        assert task.description == "Implement feature X"
        assert task.task_id

    def test_get_task(self, tmp_path):
        tm = TaskMemory(memory_dir=tmp_path / "mem", project_path=Path("/proj"))
        created = tm.create("Task A")
        fetched = tm.get(created.task_id)
        assert fetched is not None
        assert fetched.description == "Task A"

    def test_get_missing_returns_none(self, tmp_path):
        tm = TaskMemory(memory_dir=tmp_path / "mem", project_path=Path("/proj"))
        assert tm.get("nonexistent") is None

    def test_complete_task(self, tmp_path):
        tm = TaskMemory(memory_dir=tmp_path / "mem", project_path=Path("/proj"))
        task = tm.create("Task B")
        updated = tm.update_status(task.task_id, "complete")
        assert updated is not None
        assert updated.status == "complete"

    def test_list_incomplete(self, tmp_path):
        tm = TaskMemory(memory_dir=tmp_path / "mem", project_path=Path("/proj"))
        tm.create("Task 1")
        t2 = tm.create("Task 2")
        tm.update_status(t2.task_id, "complete")
        tm.create("Task 3")
        incomplete = tm.list_incomplete()
        assert len(incomplete) == 2
        assert all(t.status == "incomplete" for t in incomplete)

    def test_delete_task(self, tmp_path):
        tm = TaskMemory(memory_dir=tmp_path / "mem", project_path=Path("/proj"))
        task = tm.create("Temp task")
        tm.delete(task.task_id)
        assert tm.get(task.task_id) is None

    def test_persists_to_json_files(self, tmp_path):
        tm = TaskMemory(memory_dir=tmp_path / "mem", project_path=Path("/proj"))
        task = tm.create("Persistent task")
        # New instance should find the task
        tm2 = TaskMemory(memory_dir=tmp_path / "mem", project_path=Path("/proj"))
        fetched = tm2.get(task.task_id)
        assert fetched is not None
        assert fetched.description == "Persistent task"

    def test_task_files_stored_in_tasks_subdir(self, tmp_path):
        tm = TaskMemory(memory_dir=tmp_path / "mem", project_path=Path("/proj"))
        task = tm.create("Check location")
        task_file = tm._tasks_dir / f"{task.task_id}.json"
        assert task_file.exists()

    def test_create_with_metadata(self, tmp_path):
        tm = TaskMemory(memory_dir=tmp_path / "mem", project_path=Path("/proj"))
        task = tm.create(
            "Task with meta",
            metadata={"priority": "high", "files": ["a.py"]},
        )
        fetched = tm.get(task.task_id)
        assert fetched.metadata["priority"] == "high"


class TestLayeredMemory:
    def test_init_creates_all_layers(self, tmp_path):
        lm = LayeredMemory(
            project_root=tmp_path,
            memory_dir=tmp_path / "mem",
            project_path=Path("/proj"),
        )
        assert lm.governance is not None
        assert lm.working is not None
        assert lm.project is not None
        assert lm.tasks is not None

    def test_governance_rules_loaded(self, tmp_path):
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text("- Always test\n")
        lm = LayeredMemory(
            project_root=tmp_path,
            memory_dir=tmp_path / "mem",
            project_path=Path("/proj"),
        )
        rules = lm.get_governance_rules()
        assert len(rules) >= 1

    def test_store_to_working_memory(self, tmp_path):
        lm = LayeredMemory(
            project_root=tmp_path,
            memory_dir=tmp_path / "mem",
            project_path=Path("/proj"),
        )
        lm.working.store("scratch", "temp value")
        assert lm.working.recall("scratch") == "temp value"

    def test_store_to_project_memory(self, tmp_path):
        lm = LayeredMemory(
            project_root=tmp_path,
            memory_dir=tmp_path / "mem",
            project_path=Path("/proj"),
        )
        lm.project.store("lang", "Python", tags=("config",))
        entry = lm.project.recall("lang")
        assert entry.value == "Python"

    def test_create_task(self, tmp_path):
        lm = LayeredMemory(
            project_root=tmp_path,
            memory_dir=tmp_path / "mem",
            project_path=Path("/proj"),
        )
        task = lm.tasks.create("Do something")
        assert task.status == "incomplete"

    def test_incomplete_tasks_on_startup(self, tmp_path):
        lm = LayeredMemory(
            project_root=tmp_path,
            memory_dir=tmp_path / "mem",
            project_path=Path("/proj"),
        )
        lm.tasks.create("Unfinished work")
        # Simulate restart
        lm2 = LayeredMemory(
            project_root=tmp_path,
            memory_dir=tmp_path / "mem",
            project_path=Path("/proj"),
        )
        incomplete = lm2.get_incomplete_tasks()
        assert len(incomplete) == 1
        assert incomplete[0].description == "Unfinished work"

    def test_exposes_memory_store_for_dream(self, tmp_path):
        """DreamTask needs access to underlying MemoryStore."""
        lm = LayeredMemory(
            project_root=tmp_path,
            memory_dir=tmp_path / "mem",
            project_path=Path("/proj"),
        )
        assert lm.project.memory_store is not None
