"""Tests for TaskLifecycleManager <-> TaskOutputStore wiring (H5b wire)."""
from __future__ import annotations

from pathlib import Path

from llm_code.task.manager import TaskLifecycleManager


class TestManagerOutputStoreDefault:
    def test_output_store_disabled_by_default(self, tmp_path: Path) -> None:
        mgr = TaskLifecycleManager(task_dir=tmp_path)
        assert mgr.output_store is None
        # append_output is a no-op in this mode (keeps the old
        # behaviour where task JSONs were the only persistence).
        assert mgr.append_output("task-1", "hello") is None


class TestManagerOutputStoreWired:
    def test_output_store_wired_when_dir_given(self, tmp_path: Path) -> None:
        output_dir = tmp_path / "outputs"
        mgr = TaskLifecycleManager(task_dir=tmp_path / "tasks", output_dir=output_dir)
        assert mgr.output_store is not None
        assert output_dir.is_dir()

    def test_append_and_read_round_trip(self, tmp_path: Path) -> None:
        mgr = TaskLifecycleManager(
            task_dir=tmp_path / "tasks",
            output_dir=tmp_path / "outputs",
        )
        mgr.append_output("task-1", "line1\n")
        mgr.append_output("task-1", "line2\n")
        assert mgr.read_output("task-1") == "line1\nline2\n"

    def test_read_output_returns_empty_for_unknown(self, tmp_path: Path) -> None:
        mgr = TaskLifecycleManager(
            task_dir=tmp_path / "tasks",
            output_dir=tmp_path / "outputs",
        )
        assert mgr.read_output("task-nope") == ""

    def test_output_store_survives_manager_reconstruction(self, tmp_path: Path) -> None:
        """Output files live on disk — a fresh manager targeting the
        same dir must see the same logs."""
        out = tmp_path / "outputs"
        mgr1 = TaskLifecycleManager(task_dir=tmp_path / "tasks", output_dir=out)
        mgr1.append_output("task-1", "persisted")
        del mgr1
        mgr2 = TaskLifecycleManager(task_dir=tmp_path / "tasks", output_dir=out)
        assert mgr2.read_output("task-1") == "persisted"
