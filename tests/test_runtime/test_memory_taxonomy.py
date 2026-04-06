"""Tests for 4-type memory taxonomy system."""
from __future__ import annotations

import pytest

from llm_code.runtime.memory_taxonomy import (
    MemoryType,
    TypedMemoryEntry,
    TypedMemoryStore,
)
from llm_code.runtime.memory_validator import validate_content


# ------------------------------------------------------------------
# MemoryType
# ------------------------------------------------------------------

class TestMemoryType:
    def test_four_types(self):
        assert len(MemoryType) == 4
        assert MemoryType.USER.value == "user"
        assert MemoryType.FEEDBACK.value == "feedback"
        assert MemoryType.PROJECT.value == "project"
        assert MemoryType.REFERENCE.value == "reference"


# ------------------------------------------------------------------
# TypedMemoryEntry
# ------------------------------------------------------------------

class TestTypedMemoryEntry:
    def test_to_frontmatter_md(self):
        entry = TypedMemoryEntry(
            slug="test",
            name="Test Entry",
            description="A test",
            memory_type=MemoryType.USER,
            content="Some content here.",
            created_at="2026-01-01T00:00:00",
            updated_at="2026-01-01T00:00:00",
        )
        md = entry.to_frontmatter_md()
        assert "---" in md
        assert "name: Test Entry" in md
        assert "type: user" in md
        assert "Some content here." in md

    def test_roundtrip_file(self, tmp_path):
        entry = TypedMemoryEntry(
            slug="roundtrip",
            name="Roundtrip Test",
            description="Testing roundtrip",
            memory_type=MemoryType.FEEDBACK,
            content="Feedback content.",
            created_at="2026-01-01T00:00:00",
            updated_at="2026-01-02T00:00:00",
        )
        path = tmp_path / "roundtrip.md"
        path.write_text(entry.to_frontmatter_md(), encoding="utf-8")
        loaded = TypedMemoryEntry.from_file(path)
        assert loaded.name == "Roundtrip Test"
        assert loaded.memory_type == MemoryType.FEEDBACK
        assert loaded.content == "Feedback content."


# ------------------------------------------------------------------
# TypedMemoryStore CRUD
# ------------------------------------------------------------------

class TestTypedMemoryStoreCRUD:
    @pytest.fixture
    def store(self, tmp_path) -> TypedMemoryStore:
        return TypedMemoryStore(tmp_path / "memory")

    def test_create_and_get(self, store):
        entry = store.create(
            slug="user-role",
            name="User Role",
            description="User is a senior engineer",
            memory_type=MemoryType.USER,
            content="Senior full-stack engineer, prefers concise responses.",
        )
        assert entry.slug == "user-role"
        got = store.get("user-role")
        assert got is not None
        assert got.content == "Senior full-stack engineer, prefers concise responses."

    def test_create_duplicate_raises(self, store):
        store.create("dup", "Dup", "desc", MemoryType.USER, "content")
        with pytest.raises(FileExistsError):
            store.create("dup", "Dup", "desc", MemoryType.USER, "other")

    def test_update(self, store):
        store.create("proj", "Project", "desc", MemoryType.PROJECT, "old content")
        updated = store.update("proj", content="new content")
        assert updated.content == "new content"
        assert updated.updated_at != updated.created_at

    def test_update_nonexistent_raises(self, store):
        with pytest.raises(FileNotFoundError):
            store.update("nope", content="x")

    def test_delete(self, store):
        store.create("to-del", "Delete Me", "desc", MemoryType.REFERENCE, "content")
        store.delete("to-del")
        assert store.get("to-del") is None

    def test_delete_nonexistent_no_error(self, store):
        store.delete("nonexistent")  # should not raise

    def test_list_all(self, store):
        store.create("a", "A", "desc", MemoryType.USER, "content a")
        store.create("b", "B", "desc", MemoryType.FEEDBACK, "content b")
        store.create("c", "C", "desc", MemoryType.PROJECT, "content c")
        assert len(store.list_all()) == 3

    def test_list_by_type(self, store):
        store.create("u1", "U1", "desc", MemoryType.USER, "content")
        store.create("u2", "U2", "desc", MemoryType.USER, "content")
        store.create("p1", "P1", "desc", MemoryType.PROJECT, "content")
        assert len(store.list_by_type(MemoryType.USER)) == 2
        assert len(store.list_by_type(MemoryType.PROJECT)) == 1
        assert len(store.list_by_type(MemoryType.REFERENCE)) == 0

    def test_search(self, store):
        store.create("py", "Python Tips", "python coding", MemoryType.REFERENCE, "Use type hints.")
        store.create("go", "Go Tips", "go coding", MemoryType.REFERENCE, "Use interfaces.")
        results = store.search("python")
        assert len(results) == 1
        assert results[0].slug == "py"

    def test_search_case_insensitive(self, store):
        store.create("test", "Test", "desc", MemoryType.PROJECT, "Django REST framework")
        assert len(store.search("django")) == 1
        assert len(store.search("DJANGO")) == 1


# ------------------------------------------------------------------
# Size limits
# ------------------------------------------------------------------

class TestSizeLimits:
    @pytest.fixture
    def store(self, tmp_path) -> TypedMemoryStore:
        return TypedMemoryStore(tmp_path / "memory")

    def test_25kb_limit_enforced(self, store):
        huge_content = "x" * 30_000
        with pytest.raises(ValueError):
            store.create("huge", "Huge", "desc", MemoryType.PROJECT, huge_content)

    def test_under_limit_ok(self, store):
        content = "x" * 8_000  # under 10k validator + under 25KB file limit
        entry = store.create("ok", "OK", "desc", MemoryType.PROJECT, content)
        assert len(entry.content) == 8_000


# ------------------------------------------------------------------
# MEMORY.md index
# ------------------------------------------------------------------

class TestIndex:
    @pytest.fixture
    def store(self, tmp_path) -> TypedMemoryStore:
        return TypedMemoryStore(tmp_path / "memory")

    def test_index_created_on_write(self, store):
        store.create("test", "Test", "desc", MemoryType.USER, "content")
        index = store.get_index()
        assert "# Memory Index" in index
        assert "Test" in index
        assert "User" in index

    def test_index_updated_on_delete(self, store):
        store.create("a", "A", "desc", MemoryType.USER, "content")
        store.create("b", "B", "desc", MemoryType.USER, "content")
        store.delete("a")
        index = store.get_index()
        assert "A" not in index
        assert "B" in index

    def test_index_line_limit(self, store):
        for i in range(250):
            store.create(f"e{i:03d}", f"Entry {i}", "desc", MemoryType.PROJECT, "content")
        index = store.get_index()
        # Should be truncated
        assert "truncated" in index


# ------------------------------------------------------------------
# Validator
# ------------------------------------------------------------------

class TestValidator:
    def test_empty_content_rejected(self):
        valid, reason = validate_content("", MemoryType.USER)
        assert not valid
        assert "empty" in reason.lower()

    def test_normal_content_accepted(self):
        valid, _ = validate_content(
            "User prefers Python, works on CLI tools.", MemoryType.USER
        )
        assert valid

    def test_git_log_rejected(self):
        git_output = "\n".join(
            f"{i:07x} commit message {i}" for i in range(10)
        )
        valid, reason = validate_content(git_output, MemoryType.PROJECT)
        assert not valid
        assert "git log" in reason.lower()

    def test_file_path_heavy_rejected(self):
        paths = "\n".join(f"- /src/module{i}/index.ts" for i in range(10))
        valid, reason = validate_content(paths, MemoryType.PROJECT)
        assert not valid
        assert "file path" in reason.lower()

    def test_code_heavy_rejected(self):
        code = "Some intro.\n" + "```python\n" + "x = 1\n" * 100 + "```\n"
        valid, reason = validate_content(code, MemoryType.REFERENCE)
        assert not valid
        assert "code" in reason.lower()

    def test_too_long_rejected(self):
        valid, reason = validate_content("x" * 15_000, MemoryType.PROJECT)
        assert not valid
        assert "long" in reason.lower()

    def test_short_code_ok(self):
        """Short content with a small code block should be fine."""
        content = "Use this pattern:\n```\nresult = func()\n```\nIt's better."
        valid, _ = validate_content(content, MemoryType.REFERENCE)
        assert valid

    def test_few_paths_ok(self):
        """A few paths mixed with prose is fine."""
        content = "Key files:\n- /src/main.py\n- /src/utils.py\nThese handle the core logic."
        valid, _ = validate_content(content, MemoryType.PROJECT)
        assert valid


# ------------------------------------------------------------------
# Legacy migration
# ------------------------------------------------------------------

class TestLegacyMigration:
    def test_migrate_from_json(self, tmp_path):
        import json
        memory_dir = tmp_path / "memory"
        store = TypedMemoryStore(memory_dir)

        legacy = tmp_path / "memory.json"
        legacy.write_text(json.dumps({
            "user_role": {"value": "Senior engineer", "created_at": "2026-01-01", "updated_at": "2026-01-01", "tags": [], "relates_to": []},
            "project_stack": {"value": "Python + FastAPI", "created_at": "2026-01-01", "updated_at": "2026-01-01", "tags": [], "relates_to": []},
            "_internal": {"value": "skip this", "created_at": "2026-01-01", "updated_at": "2026-01-01", "tags": [], "relates_to": []},
        }))

        count = store.migrate_from_legacy(legacy)
        assert count == 2
        assert store.get("user_role") is not None
        assert store.get("project_stack") is not None
        # Internal keys skipped (starting with _)
        all_slugs = [e.slug for e in store.list_all()]
        assert not any(s.startswith("_") for s in all_slugs)
        # Legacy file backed up
        assert (tmp_path / "memory.json.bak").exists()
        assert not legacy.exists()

    def test_migrate_nonexistent_file(self, tmp_path):
        store = TypedMemoryStore(tmp_path / "memory")
        count = store.migrate_from_legacy(tmp_path / "nonexistent.json")
        assert count == 0
