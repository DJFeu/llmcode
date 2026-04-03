"""Tests for shared memory with file locking."""
from __future__ import annotations


import pytest

from llm_code.swarm.memory_sync import SharedMemory


@pytest.fixture
def shared_mem(tmp_path):
    return SharedMemory(tmp_path / "swarm" / "memory.json")


class TestSharedMemoryWrite:
    def test_write_creates_file(self, shared_mem, tmp_path):
        shared_mem.write("key1", "value1")
        path = tmp_path / "swarm" / "memory.json"
        assert path.exists()

    def test_write_stores_value(self, shared_mem):
        shared_mem.write("project_goal", "build a REST API")
        assert shared_mem.read("project_goal") == "build a REST API"

    def test_write_overwrites_existing(self, shared_mem):
        shared_mem.write("k", "old")
        shared_mem.write("k", "new")
        assert shared_mem.read("k") == "new"


class TestSharedMemoryRead:
    def test_read_missing_key(self, shared_mem):
        assert shared_mem.read("nonexistent") is None

    def test_read_missing_file(self, tmp_path):
        mem = SharedMemory(tmp_path / "does_not_exist.json")
        assert mem.read("k") is None


class TestSharedMemoryReadAll:
    def test_read_all_empty(self, shared_mem):
        assert shared_mem.read_all() == {}

    def test_read_all_returns_dict(self, shared_mem):
        shared_mem.write("a", "1")
        shared_mem.write("b", "2")
        result = shared_mem.read_all()
        assert result == {"a": "1", "b": "2"}


class TestSharedMemoryDelete:
    def test_delete_existing(self, shared_mem):
        shared_mem.write("k", "v")
        shared_mem.delete("k")
        assert shared_mem.read("k") is None

    def test_delete_missing_noop(self, shared_mem):
        shared_mem.delete("nonexistent")  # should not raise


class TestSharedMemoryConcurrency:
    def test_file_lock_prevents_corruption(self, tmp_path):
        """Two SharedMemory instances writing to the same file should not corrupt it."""
        path = tmp_path / "shared.json"
        mem1 = SharedMemory(path)
        mem2 = SharedMemory(path)
        mem1.write("key1", "val1")
        mem2.write("key2", "val2")
        # Both keys should be present
        assert mem1.read("key1") == "val1"
        assert mem1.read("key2") == "val2"
