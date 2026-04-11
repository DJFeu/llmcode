"""E2E: workflow / coordination commands.

/task, /cron, /swarm, /plan, /mode, /harness, /hida, /search, /orchestrate
"""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

from tests.test_e2e_tui.test_boot_banner import _rendered_text


# ── /plan (plan mode toggle) ───────────────────────────────────────────


async def test_plan_toggles_plan_mode(pilot_app):
    from llm_code.tui.chat_view import ChatScrollView
    from llm_code.tui.status_bar import StatusBar

    app, pilot = pilot_app
    status = app.query_one(StatusBar)
    chat = app.query_one(ChatScrollView)
    assert app._plan_mode is False

    app._cmd_dispatcher.dispatch("plan", "")
    await pilot.pause()
    assert app._plan_mode is True
    assert status.plan_mode == "PLAN"
    rendered = _rendered_text(chat)
    assert "Plan mode ON" in rendered

    # Toggle off.
    app._cmd_dispatcher.dispatch("plan", "")
    await pilot.pause()
    assert app._plan_mode is False
    assert status.plan_mode == ""
    rendered = _rendered_text(chat)
    assert "Plan mode OFF" in rendered


# ── /mode ──────────────────────────────────────────────────────────────


async def test_mode_bare_shows_current(pilot_app):
    from llm_code.tui.chat_view import ChatScrollView

    app, pilot = pilot_app
    chat = app.query_one(ChatScrollView)

    app._cmd_dispatcher.dispatch("mode", "")
    await pilot.pause()

    rendered = _rendered_text(chat)
    assert "Current mode" in rendered
    assert "suggest" in rendered
    assert "normal" in rendered


async def test_mode_switch_to_plan(pilot_app):
    from llm_code.tui.chat_view import ChatScrollView
    from llm_code.tui.status_bar import StatusBar

    app, pilot = pilot_app
    status = app.query_one(StatusBar)
    chat = app.query_one(ChatScrollView)

    app._cmd_dispatcher.dispatch("mode", "plan")
    await pilot.pause()

    assert app._plan_mode is True
    assert status.plan_mode == "PLAN"
    rendered = _rendered_text(chat)
    assert "Switched to plan mode" in rendered


async def test_mode_switch_to_suggest(pilot_app):
    from llm_code.tui.status_bar import StatusBar

    app, pilot = pilot_app
    status = app.query_one(StatusBar)

    app._cmd_dispatcher.dispatch("mode", "suggest")
    await pilot.pause()

    assert status.plan_mode == "SUGGEST"
    assert app._plan_mode is False


async def test_mode_unknown_prints_usage(pilot_app):
    from llm_code.tui.chat_view import ChatScrollView

    app, pilot = pilot_app
    chat = app.query_one(ChatScrollView)

    app._cmd_dispatcher.dispatch("mode", "galactic")
    await pilot.pause()

    rendered = _rendered_text(chat)
    assert "Unknown mode" in rendered


# ── /harness ───────────────────────────────────────────────────────────


async def test_harness_without_runtime_prints_fallback(pilot_app):
    from llm_code.tui.chat_view import ChatScrollView

    app, pilot = pilot_app
    app._runtime = None
    chat = app.query_one(ChatScrollView)

    app._cmd_dispatcher.dispatch("harness", "")
    await pilot.pause()

    rendered = _rendered_text(chat)
    assert "Harness not available" in rendered


# ── /search ────────────────────────────────────────────────────────────


async def test_search_bare_shows_usage(pilot_app):
    from llm_code.tui.chat_view import ChatScrollView

    app, pilot = pilot_app
    chat = app.query_one(ChatScrollView)

    app._cmd_dispatcher.dispatch("search", "")
    await pilot.pause()

    rendered = _rendered_text(chat)
    assert "Usage: /search" in rendered


async def test_search_with_query_runs_fts5(pilot_app, monkeypatch):
    """`/search foo` should call ConversationDB.search and render
    matches. Patch the DB layer so we don't hit a real FTS5 index."""
    from llm_code.tui.chat_view import ChatScrollView

    app, pilot = pilot_app

    fake_db = MagicMock()
    fake_db.search.return_value = [
        SimpleNamespace(
            conversation_id="c1234567",
            conversation_name="refactor session",
            created_at="2026-04-01T10:00:00",
            role="user",
            content_snippet="find the foo >>>thing<<< we need",
        )
    ]
    fake_db.close = MagicMock()

    monkeypatch.setattr(
        "llm_code.runtime.conversation_db.ConversationDB",
        lambda *a, **k: fake_db,
    )

    chat = app.query_one(ChatScrollView)
    app._cmd_dispatcher.dispatch("search", "foo")
    await pilot.pause()

    fake_db.search.assert_called_once()
    rendered = _rendered_text(chat)
    assert "Found 1 match" in rendered
    assert "refactor session" in rendered


async def test_search_no_matches_prints_empty(pilot_app, monkeypatch):
    from llm_code.tui.chat_view import ChatScrollView

    app, pilot = pilot_app
    fake_db = MagicMock()
    fake_db.search.return_value = []
    fake_db.close = MagicMock()

    monkeypatch.setattr(
        "llm_code.runtime.conversation_db.ConversationDB",
        lambda *a, **k: fake_db,
    )
    app._runtime = None  # disable the in-memory fallback path

    chat = app.query_one(ChatScrollView)
    app._cmd_dispatcher.dispatch("search", "nothing_to_find_here_xyz")
    await pilot.pause()

    rendered = _rendered_text(chat)
    assert "No matches" in rendered


# ── /cron ──────────────────────────────────────────────────────────────


async def test_cron_without_storage_shows_fallback(pilot_app):
    from llm_code.tui.chat_view import ChatScrollView

    app, pilot = pilot_app
    app._cron_storage = None
    chat = app.query_one(ChatScrollView)

    app._cmd_dispatcher.dispatch("cron", "")
    await pilot.pause()

    rendered = _rendered_text(chat)
    assert "Cron not available" in rendered


async def test_cron_list_empty(pilot_app):
    from llm_code.tui.chat_view import ChatScrollView

    app, pilot = pilot_app
    storage = MagicMock()
    storage.list_all.return_value = []
    app._cron_storage = storage
    chat = app.query_one(ChatScrollView)

    app._cmd_dispatcher.dispatch("cron", "list")
    await pilot.pause()

    rendered = _rendered_text(chat)
    assert "No scheduled tasks" in rendered


async def test_cron_list_renders_tasks(pilot_app):
    from llm_code.tui.chat_view import ChatScrollView

    app, pilot = pilot_app
    storage = MagicMock()
    storage.list_all.return_value = [
        SimpleNamespace(
            id="t001",
            cron="0 9 * * *",
            prompt="morning standup",
            recurring=True,
            permanent=False,
            last_fired_at=datetime(2026, 4, 10, 9, 0, 0),
        ),
    ]
    app._cron_storage = storage
    chat = app.query_one(ChatScrollView)

    app._cmd_dispatcher.dispatch("cron", "list")
    await pilot.pause()

    rendered = _rendered_text(chat)
    assert "t001" in rendered
    assert "0 9 * * *" in rendered
    assert "morning standup" in rendered
    assert "recurring" in rendered


async def test_cron_delete_calls_remove(pilot_app):
    from llm_code.tui.chat_view import ChatScrollView

    app, pilot = pilot_app
    storage = MagicMock()
    storage.remove.return_value = True
    app._cron_storage = storage
    chat = app.query_one(ChatScrollView)

    app._cmd_dispatcher.dispatch("cron", "delete t001")
    await pilot.pause()

    storage.remove.assert_called_once_with("t001")
    rendered = _rendered_text(chat)
    assert "Deleted task t001" in rendered


async def test_cron_delete_missing_prints_not_found(pilot_app):
    from llm_code.tui.chat_view import ChatScrollView

    app, pilot = pilot_app
    storage = MagicMock()
    storage.remove.return_value = False
    app._cron_storage = storage
    chat = app.query_one(ChatScrollView)

    app._cmd_dispatcher.dispatch("cron", "delete nope")
    await pilot.pause()

    rendered = _rendered_text(chat)
    assert "not found" in rendered


# ── /task ──────────────────────────────────────────────────────────────


async def test_task_list_empty(pilot_app):
    from llm_code.tui.chat_view import ChatScrollView

    app, pilot = pilot_app
    mgr = MagicMock()
    mgr.list_tasks.return_value = []
    app._task_manager = mgr
    chat = app.query_one(ChatScrollView)

    app._cmd_dispatcher.dispatch("task", "list")
    await pilot.pause()

    rendered = _rendered_text(chat)
    assert "No tasks found" in rendered


async def test_task_list_renders_entries(pilot_app):
    from llm_code.tui.chat_view import ChatScrollView

    app, pilot = pilot_app
    mgr = MagicMock()
    mgr.list_tasks.return_value = [
        SimpleNamespace(
            id="T1",
            status=SimpleNamespace(value="planned"),
            title="refactor the voice flow",
        ),
        SimpleNamespace(
            id="T2",
            status=SimpleNamespace(value="done"),
            title="ship v1.22.0",
        ),
    ]
    app._task_manager = mgr
    chat = app.query_one(ChatScrollView)

    app._cmd_dispatcher.dispatch("task", "list")
    await pilot.pause()

    rendered = _rendered_text(chat)
    assert "T1" in rendered
    assert "T2" in rendered
    assert "refactor the voice flow" in rendered
    assert "planned" in rendered


async def test_task_list_without_manager(pilot_app):
    from llm_code.tui.chat_view import ChatScrollView

    app, pilot = pilot_app
    app._task_manager = None
    chat = app.query_one(ChatScrollView)

    app._cmd_dispatcher.dispatch("task", "list")
    await pilot.pause()

    rendered = _rendered_text(chat)
    assert "Task manager not initialized" in rendered


async def test_task_new_prints_guidance(pilot_app):
    from llm_code.tui.chat_view import ChatScrollView

    app, pilot = pilot_app
    chat = app.query_one(ChatScrollView)

    app._cmd_dispatcher.dispatch("task", "new")
    await pilot.pause()

    rendered = _rendered_text(chat)
    assert "task tools" in rendered


# ── /swarm ─────────────────────────────────────────────────────────────


async def test_swarm_without_manager_shows_disabled(pilot_app):
    from llm_code.tui.chat_view import ChatScrollView

    app, pilot = pilot_app
    app._swarm_manager = None
    chat = app.query_one(ChatScrollView)

    app._cmd_dispatcher.dispatch("swarm", "")
    await pilot.pause()

    rendered = _rendered_text(chat)
    assert "not enabled" in rendered


async def test_swarm_with_manager_shows_active(pilot_app):
    from llm_code.tui.chat_view import ChatScrollView

    app, pilot = pilot_app
    app._swarm_manager = MagicMock()
    chat = app.query_one(ChatScrollView)

    app._cmd_dispatcher.dispatch("swarm", "")
    await pilot.pause()

    rendered = _rendered_text(chat)
    assert "Swarm: active" in rendered


async def test_swarm_coordinate_without_task_prints_usage(pilot_app):
    from llm_code.tui.chat_view import ChatScrollView

    app, pilot = pilot_app
    app._swarm_manager = MagicMock()
    chat = app.query_one(ChatScrollView)

    app._cmd_dispatcher.dispatch("swarm", "coordinate")
    await pilot.pause()

    rendered = _rendered_text(chat)
    assert "Usage: /swarm coordinate" in rendered


# ── /orchestrate ───────────────────────────────────────────────────────


async def test_orchestrate_without_task_prints_usage(pilot_app):
    from llm_code.tui.chat_view import ChatScrollView

    app, pilot = pilot_app
    chat = app.query_one(ChatScrollView)

    app._cmd_dispatcher.dispatch("orchestrate", "")
    await pilot.pause()

    rendered = _rendered_text(chat)
    assert "Usage: /orchestrate" in rendered


async def test_orchestrate_without_runtime_prints_fallback(pilot_app):
    from llm_code.tui.chat_view import ChatScrollView

    app, pilot = pilot_app
    app._runtime = None
    chat = app.query_one(ChatScrollView)

    app._cmd_dispatcher.dispatch("orchestrate", "write a function")
    await pilot.pause()

    rendered = _rendered_text(chat)
    assert "runtime not ready" in rendered


async def test_orchestrate_dispatches_worker(pilot_app):

    app, pilot = pilot_app
    app._runtime = MagicMock()

    # Intercept run_worker — we don't want the real orchestrator to
    # run, just to assert the command reached the dispatch point.
    called = {"work": None}

    def _track(work, *args, **kwargs):
        called["work"] = work
        if hasattr(work, "close"):
            work.close()
        return None

    app.run_worker = _track  # type: ignore[assignment]

    app._cmd_dispatcher.dispatch("orchestrate", "refactor the parser")
    await pilot.pause()

    assert called["work"] is not None


# ── /hida ──────────────────────────────────────────────────────────────


async def test_hida_without_runtime_shows_not_initialized(pilot_app):
    from llm_code.tui.chat_view import ChatScrollView

    app, pilot = pilot_app
    app._runtime = None
    chat = app.query_one(ChatScrollView)

    app._cmd_dispatcher.dispatch("hida", "")
    await pilot.pause()

    rendered = _rendered_text(chat)
    assert "HIDA" in rendered
    assert "not initialized" in rendered


async def test_hida_with_runtime_no_profile(pilot_app):
    from llm_code.tui.chat_view import ChatScrollView

    app, pilot = pilot_app
    runtime = MagicMock()
    runtime._last_hida_profile = None
    app._runtime = runtime
    chat = app.query_one(ChatScrollView)

    app._cmd_dispatcher.dispatch("hida", "")
    await pilot.pause()

    rendered = _rendered_text(chat)
    # Either "enabled" or "disabled" + "no classification yet" —
    # depends on config.hida.enabled, but both paths exercise
    # the same handler successfully.
    assert "HIDA" in rendered
    assert "classification" in rendered or "initialized" in rendered


async def test_hida_with_profile_shows_summary(pilot_app):
    from llm_code.runtime.hida import HidaEngine, TaskProfile, TaskType
    from llm_code.tui.chat_view import ChatScrollView

    app, pilot = pilot_app
    profile = TaskProfile(
        task_type=TaskType.CODING,
        confidence=0.92,
        tools=frozenset({"bash"}),
        memory_keys=frozenset(),
        governance_categories=frozenset({"coding"}),
        load_full_prompt=False,
    )
    runtime = MagicMock()
    runtime._last_hida_profile = profile
    app._runtime = runtime
    chat = app.query_one(ChatScrollView)

    app._cmd_dispatcher.dispatch("hida", "")
    await pilot.pause()

    rendered = _rendered_text(chat)
    assert "HIDA:" in rendered
    # HidaEngine.build_summary includes the task type.
    expected = HidaEngine().build_summary(profile)
    assert expected[:30] in rendered
