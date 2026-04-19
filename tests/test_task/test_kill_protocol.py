"""Tests for the task kill Protocol + output store (H5b — Sprint 3).

Skeleton for Claude Code's multi-type kill pattern (Task.ts —
local_bash / local_agent / remote_agent / in_process_teammate / dream
all implement ``.kill()``). Lands the Protocol plus a small output
store so ``TaskState`` can keep a pointer instead of inlining large
logs. Wiring the existing task types into the Protocol is a
follow-up.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from llm_code.task.kill_protocol import (
    KillResult,
    TaskKiller,
    TaskOutputStore,
)


# ---------- TaskKiller Protocol ----------


class TestTaskKiller:
    def test_stub_killer_satisfies_protocol(self) -> None:
        class Stub:
            task_type = "stub"

            def kill(self, reason: str) -> KillResult:
                return KillResult(killed=True, reason=reason)

        assert isinstance(Stub(), TaskKiller)

    def test_missing_kill_method_does_not_satisfy(self) -> None:
        class NotAKiller:
            task_type = "nope"

        assert not isinstance(NotAKiller(), TaskKiller)


class TestKillResult:
    def test_frozen(self) -> None:
        r = KillResult(killed=True, reason="user requested")
        with pytest.raises(Exception):
            r.killed = False  # type: ignore[misc]

    def test_success_and_failure(self) -> None:
        ok = KillResult(killed=True, reason="x")
        bad = KillResult(killed=False, reason="already gone")
        assert ok.killed is True
        assert bad.killed is False


# ---------- TaskOutputStore ----------


class TestTaskOutputStore:
    def test_append_and_read(self, tmp_path: Path) -> None:
        store = TaskOutputStore(tmp_path)
        store.append("task-1", "hello\n")
        store.append("task-1", "world\n")
        text = store.read("task-1")
        assert text == "hello\nworld\n"

    def test_path_for_task(self, tmp_path: Path) -> None:
        store = TaskOutputStore(tmp_path)
        store.append("task-1", "x")
        path = store.path_for("task-1")
        assert path.is_file()
        assert path.read_text() == "x"
        assert path.parent == tmp_path

    def test_tail_returns_last_n_chars(self, tmp_path: Path) -> None:
        store = TaskOutputStore(tmp_path)
        store.append("task-1", "abcdefghij")
        assert store.tail("task-1", n=4) == "ghij"

    def test_tail_of_missing_is_empty(self, tmp_path: Path) -> None:
        store = TaskOutputStore(tmp_path)
        assert store.tail("task-nope", n=10) == ""

    def test_offset_advances_on_append(self, tmp_path: Path) -> None:
        store = TaskOutputStore(tmp_path)
        store.append("task-1", "ab")
        o1 = store.offset("task-1")
        store.append("task-1", "cd")
        o2 = store.offset("task-1")
        assert o2 == o1 + 2

    def test_clear(self, tmp_path: Path) -> None:
        store = TaskOutputStore(tmp_path)
        store.append("task-1", "x")
        store.clear("task-1")
        assert store.path_for("task-1").exists() is False
        assert store.offset("task-1") == 0

    def test_unsafe_task_id_rejected(self, tmp_path: Path) -> None:
        """Task IDs that would escape the store directory must be rejected
        so a malicious caller can't write outside the expected folder."""
        store = TaskOutputStore(tmp_path)
        with pytest.raises(ValueError):
            store.append("../outside", "x")
        with pytest.raises(ValueError):
            store.append("a/b", "x")
