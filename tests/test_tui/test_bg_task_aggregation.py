"""Headless test for background-task count aggregation logic.

Mirrors what TUIApp._poll_bg_tasks does without spinning up Textual:
combines TaskLifecycleManager.running_task_count() with
AsyncTaskRegistry.active_count().
"""
from __future__ import annotations

import asyncio

import pytest

from llm_code.runtime.background_task_registry import AsyncTaskRegistry
from llm_code.task.manager import TaskLifecycleManager
from llm_code.tui.status_bar import StatusBar


def _aggregate(mgr: TaskLifecycleManager, reg: AsyncTaskRegistry) -> int:
    return mgr.running_task_count() + reg.active_count()


def test_aggregation_zero_when_idle(tmp_path):
    mgr = TaskLifecycleManager(tmp_path / "tasks")
    reg = AsyncTaskRegistry()
    assert _aggregate(mgr, reg) == 0


def test_aggregation_counts_lifecycle_only(tmp_path):
    mgr = TaskLifecycleManager(tmp_path / "tasks")
    reg = AsyncTaskRegistry()
    mgr.create_task("plan", "desc")  # PLAN is active
    assert _aggregate(mgr, reg) == 1


@pytest.mark.asyncio
async def test_aggregation_counts_async_tasks(tmp_path):
    mgr = TaskLifecycleManager(tmp_path / "tasks")
    reg = AsyncTaskRegistry()

    started = asyncio.Event()
    release = asyncio.Event()

    async def _w() -> None:
        started.set()
        await release.wait()

    task = asyncio.create_task(_w())
    reg.register(task, "w")
    await started.wait()
    assert _aggregate(mgr, reg) == 1

    release.set()
    await task
    await asyncio.sleep(0)
    assert _aggregate(mgr, reg) == 0


def test_status_bar_renders_aggregated_count():
    bar = StatusBar()
    bar.bg_tasks = 4
    assert "4 tasks running" in bar._format_content()
