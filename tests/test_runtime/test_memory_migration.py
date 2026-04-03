"""Tests for auto-migration of legacy memory.json to L2 ProjectMemory."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


from llm_code.runtime.memory_layers import LayeredMemory


class TestLegacyMigration:
    def _write_legacy_memory(self, memory_dir: Path, project_path: Path) -> Path:
        """Write a legacy memory.json in the old MemoryStore format."""
        project_hash = hashlib.sha256(str(project_path).encode()).hexdigest()[:8]
        legacy_dir = memory_dir / project_hash
        legacy_dir.mkdir(parents=True, exist_ok=True)
        legacy_file = legacy_dir / "memory.json"
        legacy_data = {
            "project_lang": {
                "value": "Python",
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-02T00:00:00+00:00",
            },
            "framework": {
                "value": "FastAPI",
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-03T00:00:00+00:00",
            },
        }
        legacy_file.write_text(json.dumps(legacy_data, indent=2))
        return legacy_file

    def test_legacy_data_accessible_via_project_memory(self, tmp_path):
        """Existing memory.json entries are readable through ProjectMemory."""
        project_path = Path("/proj/legacy")
        self._write_legacy_memory(tmp_path / "mem", project_path)
        lm = LayeredMemory(
            project_root=tmp_path,
            memory_dir=tmp_path / "mem",
            project_path=project_path,
        )
        entry = lm.project.recall("project_lang")
        assert entry is not None
        assert entry.value == "Python"

    def test_legacy_entries_get_empty_tags(self, tmp_path):
        """Legacy entries without tags metadata get empty tag tuples."""
        project_path = Path("/proj/legacy")
        self._write_legacy_memory(tmp_path / "mem", project_path)
        lm = LayeredMemory(
            project_root=tmp_path,
            memory_dir=tmp_path / "mem",
            project_path=project_path,
        )
        entry = lm.project.recall("project_lang")
        assert entry.tags == ()

    def test_legacy_entries_in_get_all(self, tmp_path):
        """get_all includes legacy entries."""
        project_path = Path("/proj/legacy")
        self._write_legacy_memory(tmp_path / "mem", project_path)
        lm = LayeredMemory(
            project_root=tmp_path,
            memory_dir=tmp_path / "mem",
            project_path=project_path,
        )
        all_entries = lm.project.get_all()
        assert "project_lang" in all_entries
        assert "framework" in all_entries

    def test_new_store_with_tags_alongside_legacy(self, tmp_path):
        """New entries with tags coexist with legacy entries."""
        project_path = Path("/proj/legacy")
        self._write_legacy_memory(tmp_path / "mem", project_path)
        lm = LayeredMemory(
            project_root=tmp_path,
            memory_dir=tmp_path / "mem",
            project_path=project_path,
        )
        lm.project.store("new_key", "new_value", tags=("added",))
        # Legacy still works
        assert lm.project.recall("project_lang").value == "Python"
        # New entry has tags
        new_entry = lm.project.recall("new_key")
        assert new_entry.value == "new_value"
        assert "added" in new_entry.tags

    def test_no_migration_file_created(self, tmp_path):
        """Migration is transparent — no separate migration marker needed.

        ProjectMemory wraps MemoryStore, so legacy data is always accessible.
        Tags metadata is stored in a separate tags.json file.
        """
        project_path = Path("/proj/legacy")
        self._write_legacy_memory(tmp_path / "mem", project_path)
        lm = LayeredMemory(
            project_root=tmp_path,
            memory_dir=tmp_path / "mem",
            project_path=project_path,
        )
        # tags.json should not exist until we store with tags
        project_hash = hashlib.sha256(str(project_path).encode()).hexdigest()[:8]
        tags_file = tmp_path / "mem" / project_hash / "tags.json"
        assert not tags_file.exists()
        # After storing with tags, tags.json is created
        lm.project.store("k", "v", tags=("t",))
        assert tags_file.exists()
