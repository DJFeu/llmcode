"""E2E: session + memory + undo + diff + compact commands."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from tests.test_e2e_tui.test_boot_banner import _rendered_text


# ── /session ───────────────────────────────────────────────────────────


async def test_session_prints_usage_hint(pilot_app):
    """`/session` is a stub pointer to /session list|save."""
    from llm_code.tui.chat_view import ChatScrollView

    app, pilot = pilot_app
    chat = app.query_one(ChatScrollView)

    app._cmd_dispatcher.dispatch("session", "")
    await pilot.pause()

    rendered = _rendered_text(chat)
    assert "Session management" in rendered


# ── /memory ────────────────────────────────────────────────────────────


async def test_memory_without_store_prints_fallback(pilot_app):
    from llm_code.tui.chat_view import ChatScrollView

    app, pilot = pilot_app
    app._memory = None
    chat = app.query_one(ChatScrollView)

    app._cmd_dispatcher.dispatch("memory", "")
    await pilot.pause()

    rendered = _rendered_text(chat)
    assert "Memory not initialized" in rendered


async def test_memory_set_stores_value(pilot_app):
    from llm_code.tui.chat_view import ChatScrollView

    app, pilot = pilot_app
    store = MagicMock()
    store.store = MagicMock()
    app._memory = store
    chat = app.query_one(ChatScrollView)

    app._cmd_dispatcher.dispatch("memory", "set project_stack python+textual")
    await pilot.pause()

    store.store.assert_called_once_with("project_stack", "python+textual")
    rendered = _rendered_text(chat)
    assert "Stored: project_stack" in rendered


async def test_memory_get_returns_value_or_not_found(pilot_app):
    from llm_code.tui.chat_view import ChatScrollView

    app, pilot = pilot_app
    store = MagicMock()
    store.recall = MagicMock(side_effect=lambda k: "python+textual" if k == "stack" else None)
    app._memory = store
    chat = app.query_one(ChatScrollView)

    # Known key.
    app._cmd_dispatcher.dispatch("memory", "get stack")
    await pilot.pause()
    rendered = _rendered_text(chat)
    assert "python+textual" in rendered

    # Unknown key.
    await chat.remove_children()
    await pilot.pause()
    app._cmd_dispatcher.dispatch("memory", "get unknown")
    await pilot.pause()
    rendered = _rendered_text(chat)
    assert "Key not found: unknown" in rendered


async def test_memory_delete_calls_store_delete(pilot_app):
    from llm_code.tui.chat_view import ChatScrollView

    app, pilot = pilot_app
    store = MagicMock()
    store.delete = MagicMock()
    app._memory = store
    chat = app.query_one(ChatScrollView)

    app._cmd_dispatcher.dispatch("memory", "delete stack")
    await pilot.pause()

    store.delete.assert_called_once_with("stack")
    rendered = _rendered_text(chat)
    assert "Deleted: stack" in rendered


async def test_memory_bare_lists_entries(pilot_app):
    """`/memory` with no sub-command should call get_all and list
    all entries with their first-60-char preview."""
    from llm_code.tui.chat_view import ChatScrollView

    app, pilot = pilot_app
    store = MagicMock()
    entry1 = SimpleNamespace(value="python + textual runtime")
    entry2 = SimpleNamespace(value="Adam is the primary user")
    store.get_all.return_value = {"project_stack": entry1, "user_profile": entry2}
    app._memory = store
    chat = app.query_one(ChatScrollView)

    app._cmd_dispatcher.dispatch("memory", "")
    await pilot.pause()

    rendered = _rendered_text(chat)
    assert "Memory (2 entries)" in rendered
    assert "project_stack" in rendered
    assert "user_profile" in rendered


async def test_memory_empty_bare_shows_no_entries(pilot_app):
    from llm_code.tui.chat_view import ChatScrollView

    app, pilot = pilot_app
    store = MagicMock()
    store.get_all.return_value = {}
    app._memory = store
    chat = app.query_one(ChatScrollView)

    app._cmd_dispatcher.dispatch("memory", "")
    await pilot.pause()

    rendered = _rendered_text(chat)
    assert "No memories stored" in rendered


# ── /undo ──────────────────────────────────────────────────────────────


async def test_undo_without_checkpoint_manager_shows_fallback(pilot_app):
    from llm_code.tui.chat_view import ChatScrollView

    app, pilot = pilot_app
    app._checkpoint_mgr = None
    chat = app.query_one(ChatScrollView)

    app._cmd_dispatcher.dispatch("undo", "")
    await pilot.pause()

    rendered = _rendered_text(chat)
    assert "undo not available" in rendered or "git repository" in rendered


async def test_undo_nothing_to_undo(pilot_app):
    from llm_code.tui.chat_view import ChatScrollView

    app, pilot = pilot_app
    mgr = MagicMock()
    mgr.can_undo.return_value = False
    app._checkpoint_mgr = mgr
    chat = app.query_one(ChatScrollView)

    app._cmd_dispatcher.dispatch("undo", "")
    await pilot.pause()

    rendered = _rendered_text(chat)
    assert "Nothing to undo" in rendered


async def test_undo_list_shows_checkpoints(pilot_app):
    from llm_code.tui.chat_view import ChatScrollView

    app, pilot = pilot_app
    mgr = MagicMock()
    mgr.list_checkpoints.return_value = [
        SimpleNamespace(id="cp1", tool_name="edit_file", timestamp="2026-04-11T10:00:00"),
        SimpleNamespace(id="cp2", tool_name="bash", timestamp="2026-04-11T10:05:00"),
    ]
    app._checkpoint_mgr = mgr
    chat = app.query_one(ChatScrollView)

    app._cmd_dispatcher.dispatch("undo", "list")
    await pilot.pause()

    rendered = _rendered_text(chat)
    assert "cp1" in rendered
    assert "edit_file" in rendered
    assert "cp2" in rendered


async def test_undo_single_step_calls_undo_once(pilot_app):
    from llm_code.tui.chat_view import ChatScrollView

    app, pilot = pilot_app
    mgr = MagicMock()
    mgr.can_undo.return_value = True
    mgr.undo.return_value = SimpleNamespace(
        tool_name="edit_file",
        tool_args_summary="foo.py",
    )
    app._checkpoint_mgr = mgr
    chat = app.query_one(ChatScrollView)

    app._cmd_dispatcher.dispatch("undo", "")
    await pilot.pause()

    mgr.undo.assert_called_once_with(1)
    rendered = _rendered_text(chat)
    assert "Undone" in rendered
    assert "edit_file" in rendered


async def test_undo_multi_step(pilot_app):
    app, pilot = pilot_app
    mgr = MagicMock()
    mgr.can_undo.return_value = True
    mgr.undo.return_value = SimpleNamespace(tool_name="write_file", tool_args_summary="x")
    app._checkpoint_mgr = mgr

    app._cmd_dispatcher.dispatch("undo", "3")
    await pilot.pause()

    mgr.undo.assert_called_once_with(3)


# ── /diff ──────────────────────────────────────────────────────────────


async def test_diff_without_checkpoints_shows_fallback(pilot_app):
    from llm_code.tui.chat_view import ChatScrollView

    app, pilot = pilot_app
    app._checkpoint_mgr = None
    chat = app.query_one(ChatScrollView)

    app._cmd_dispatcher.dispatch("diff", "")
    await pilot.pause()

    rendered = _rendered_text(chat)
    assert "No checkpoints available" in rendered


async def test_diff_with_checkpoint_runs_git_diff(pilot_app, monkeypatch):
    """When checkpoints exist, /diff should shell out to git diff
    between the last checkpoint sha and HEAD."""
    from llm_code.tui.chat_view import ChatScrollView

    app, pilot = pilot_app

    mgr = MagicMock()
    mgr.can_undo.return_value = True
    mgr.list_checkpoints.return_value = [
        SimpleNamespace(id="cp1", git_sha="abc123"),
    ]
    app._checkpoint_mgr = mgr

    # Stub subprocess.run so we don't actually invoke git.
    fake_result = SimpleNamespace(
        stdout="diff --git a/foo b/foo\n+new line\n",
        returncode=0,
    )
    monkeypatch.setattr(
        "llm_code.tui.command_dispatcher.subprocess.run",
        lambda *a, **kw: fake_result,
    )

    chat = app.query_one(ChatScrollView)
    app._cmd_dispatcher.dispatch("diff", "")
    await pilot.pause()

    rendered = _rendered_text(chat)
    assert "new line" in rendered or "diff --git" in rendered


async def test_diff_no_changes_shows_clean_message(pilot_app, monkeypatch):
    from llm_code.tui.chat_view import ChatScrollView

    app, pilot = pilot_app

    mgr = MagicMock()
    mgr.can_undo.return_value = True
    mgr.list_checkpoints.return_value = [
        SimpleNamespace(id="cp1", git_sha="abc123"),
    ]
    app._checkpoint_mgr = mgr

    monkeypatch.setattr(
        "llm_code.tui.command_dispatcher.subprocess.run",
        lambda *a, **kw: SimpleNamespace(stdout="   ", returncode=0),
    )

    chat = app.query_one(ChatScrollView)
    app._cmd_dispatcher.dispatch("diff", "")
    await pilot.pause()

    rendered = _rendered_text(chat)
    assert "No changes" in rendered


# ── /compact ───────────────────────────────────────────────────────────


async def test_compact_without_runtime_shows_fallback(pilot_app):
    from llm_code.tui.chat_view import ChatScrollView

    app, pilot = pilot_app
    app._runtime = None
    chat = app.query_one(ChatScrollView)

    app._cmd_dispatcher.dispatch("compact", "")
    await pilot.pause()

    rendered = _rendered_text(chat)
    assert "Compaction unavailable" in rendered or "runtime not initialized" in rendered


async def test_compact_with_runtime_calls_compact_session(pilot_app, monkeypatch):
    from llm_code.tui.chat_view import ChatScrollView

    app, pilot = pilot_app

    # Minimal fake runtime with a mutable session.
    fake_compacted = SimpleNamespace(
        messages=["compacted"],
        estimated_tokens=lambda: 500,
    )
    runtime = MagicMock()
    runtime.session = SimpleNamespace(
        messages=["m1", "m2", "m3", "m4", "m5", "m6"],
        estimated_tokens=lambda: 5000,
    )
    app._runtime = runtime

    called = {}

    def _fake_compact(session, *, keep_recent, summary):
        called["keep_recent"] = keep_recent
        called["summary"] = summary
        return fake_compacted

    monkeypatch.setattr(
        "llm_code.runtime.compaction.compact_session", _fake_compact
    )

    chat = app.query_one(ChatScrollView)
    app._cmd_dispatcher.dispatch("compact", "2")
    await pilot.pause()

    assert called["keep_recent"] == 2
    assert "manual" in called["summary"].lower()
    rendered = _rendered_text(chat)
    assert "Compacted" in rendered


async def test_compact_default_keep_is_4(pilot_app, monkeypatch):

    app, pilot = pilot_app

    runtime = MagicMock()
    runtime.session = SimpleNamespace(
        messages=["m"] * 10,
        estimated_tokens=lambda: 1000,
    )
    app._runtime = runtime

    called = {}

    def _fake_compact(session, *, keep_recent, summary):
        called["keep_recent"] = keep_recent
        return SimpleNamespace(
            messages=["m"] * 4,
            estimated_tokens=lambda: 400,
        )

    monkeypatch.setattr(
        "llm_code.runtime.compaction.compact_session", _fake_compact
    )

    app._cmd_dispatcher.dispatch("compact", "")
    await pilot.pause()

    assert called["keep_recent"] == 4
