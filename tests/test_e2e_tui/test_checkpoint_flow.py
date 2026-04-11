"""E2E: `/checkpoint save` / `/checkpoint list` / `/checkpoint resume`
with cost_tracker round-trip."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from tests.test_e2e_tui.test_boot_banner import _rendered_text


def _fake_session(session_id="ckpt01", messages=()):
    """Minimal Session-compatible stand-in.

    Supplies exactly the fields ``Session.to_dict()`` and
    ``CheckpointRecovery.save_checkpoint()`` read, and nothing else.
    Bypasses ``load_and_migrate`` by making messages a serializable
    list of dicts when to_dict is called.
    """
    return SimpleNamespace(
        id=session_id,
        messages=messages,
        created_at="2026-04-11T00:00:00+00:00",
        updated_at="2026-04-11T06:00:00+00:00",
        total_usage=SimpleNamespace(input_tokens=42, output_tokens=21),
        project_path=Path("/tmp/project"),
        name="",
        tags=(),
        to_dict=lambda: {
            "_schema_version": 3,
            "id": session_id,
            "messages": [],
            "created_at": "2026-04-11T00:00:00+00:00",
            "updated_at": "2026-04-11T06:00:00+00:00",
            "total_usage": {"input_tokens": 42, "output_tokens": 21},
            "project_path": "/tmp/project",
            "name": "",
            "tags": [],
        },
    )


async def test_checkpoint_save_writes_file(pilot_app, tmp_path, monkeypatch):
    """`/checkpoint save` should write the active session to
    ~/.llmcode/checkpoints/<id>.json and print the path."""
    from llm_code.tui.chat_view import ChatScrollView

    app, pilot = pilot_app
    # Point the checkpoint store at tmp_path so we don't touch the
    # user's real ~/.llmcode.
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    # Attach a mock runtime with a session.
    app._runtime = MagicMock()
    app._runtime.session = _fake_session(session_id="e2esave")

    app._cmd_dispatcher.dispatch("checkpoint", "save")
    await pilot.pause()

    # Side effect: a file exists under the checkpoints dir.
    ckpt_file = tmp_path / ".llmcode" / "checkpoints" / "e2esave.json"
    assert ckpt_file.exists()

    # Confirmation message in chat.
    chat = app.query_one(ChatScrollView)
    rendered = _rendered_text(chat)
    assert "Checkpoint saved" in rendered
    assert "e2esave" in rendered


async def test_checkpoint_save_without_runtime_errors_gracefully(
    pilot_app, tmp_path, monkeypatch
):
    """With no runtime attached, save must print an error, not crash."""
    from llm_code.tui.chat_view import ChatScrollView

    app, pilot = pilot_app
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    app._runtime = None

    app._cmd_dispatcher.dispatch("checkpoint", "save")
    await pilot.pause()

    chat = app.query_one(ChatScrollView)
    rendered = _rendered_text(chat)
    assert "No active session" in rendered


async def test_checkpoint_list_empty(pilot_app, tmp_path, monkeypatch):
    from llm_code.tui.chat_view import ChatScrollView

    app, pilot = pilot_app
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    app._cmd_dispatcher.dispatch("checkpoint", "list")
    await pilot.pause()

    chat = app.query_one(ChatScrollView)
    rendered = _rendered_text(chat)
    assert "No checkpoints found" in rendered


async def test_checkpoint_resume_restores_cost_tracker(
    pilot_app, tmp_path, monkeypatch
):
    """Wave2-2: a checkpoint saved with a cost_tracker payload should,
    on resume, populate the live cost_tracker with the running totals
    from disk instead of starting fresh at zero."""
    from llm_code.runtime.checkpoint_recovery import CheckpointRecovery
    from llm_code.runtime.cost_tracker import CostTracker

    app, pilot = pilot_app
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    # Seed a checkpoint on disk with a known cost state.
    checkpoints_dir = tmp_path / ".llmcode" / "checkpoints"
    recovery = CheckpointRecovery(checkpoints_dir)
    saved_tracker = CostTracker(model="claude-sonnet")
    saved_tracker.total_input_tokens = 1234
    saved_tracker.total_output_tokens = 567
    saved_tracker.total_cost_usd = 0.0321
    recovery.save_checkpoint(
        _fake_session(session_id="resume01"), cost_tracker=saved_tracker
    )

    # Pilot app gets a FRESH cost tracker — all zeros.
    live_tracker = CostTracker(model="claude-sonnet")
    assert live_tracker.total_input_tokens == 0
    app._cost_tracker = live_tracker

    # Resume.
    app._cmd_dispatcher.dispatch("checkpoint", "resume resume01")
    await pilot.pause()

    # Totals restored.
    assert live_tracker.total_input_tokens == 1234
    assert live_tracker.total_output_tokens == 567
    assert abs(live_tracker.total_cost_usd - 0.0321) < 1e-9


async def test_checkpoint_resume_nonexistent_session(
    pilot_app, tmp_path, monkeypatch
):
    """Resuming a missing checkpoint id should print an error."""
    from llm_code.tui.chat_view import ChatScrollView

    app, pilot = pilot_app
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    app._cmd_dispatcher.dispatch("checkpoint", "resume nope-nonexistent")
    await pilot.pause()

    chat = app.query_one(ChatScrollView)
    rendered = _rendered_text(chat)
    assert "No checkpoint found" in rendered
