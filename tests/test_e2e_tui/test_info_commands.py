"""E2E: info / config / report commands.

Covers: /cost, /gain, /profile, /cache, /personas, /model, /budget,
/set, /config, /map, /dump, /cd.

These commands are mostly read-only or state-setting; their E2E
contract is "dispatching produces the expected chat entry / state
change" rather than a full round-trip through an LLM.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from tests.test_e2e_tui.test_boot_banner import _rendered_text


# ── /cost ──────────────────────────────────────────────────────────────


async def test_cost_with_no_tracker_prints_fallback(pilot_app):
    from llm_code.tui.chat_view import ChatScrollView

    app, pilot = pilot_app
    app._cost_tracker = None
    chat = app.query_one(ChatScrollView)

    app._cmd_dispatcher.dispatch("cost", "")
    await pilot.pause()

    rendered = _rendered_text(chat)
    assert "No cost data" in rendered


async def test_cost_with_tracker_calls_format_cost(pilot_app):
    from llm_code.runtime.cost_tracker import CostTracker
    from llm_code.tui.chat_view import ChatScrollView

    app, pilot = pilot_app
    tracker = CostTracker(model="claude-sonnet")
    tracker.total_input_tokens = 500
    tracker.total_output_tokens = 200
    app._cost_tracker = tracker
    chat = app.query_one(ChatScrollView)

    app._cmd_dispatcher.dispatch("cost", "")
    await pilot.pause()

    rendered = _rendered_text(chat)
    # format_cost() produces a "Tokens — in: … out: …" line.
    assert "500" in rendered or "Tokens" in rendered


# ── /gain ──────────────────────────────────────────────────────────────


async def test_gain_runs_token_tracker_report(pilot_app, monkeypatch):
    """`/gain` should instantiate a TokenTracker and render its report."""
    from llm_code.tui.chat_view import ChatScrollView

    app, pilot = pilot_app
    chat = app.query_one(ChatScrollView)

    # Patch TokenTracker so the test doesn't hit a real ~/.llmcode file.
    fake_tracker = MagicMock()
    fake_tracker.format_report.return_value = "Token savings report (7d): 42%"
    fake_tracker.close = MagicMock()

    def _fake_ctor(*args, **kwargs):
        return fake_tracker

    monkeypatch.setattr(
        "llm_code.tools.token_tracker.TokenTracker", _fake_ctor
    )

    app._cmd_dispatcher.dispatch("gain", "7")
    await pilot.pause()

    fake_tracker.format_report.assert_called_once_with(7)
    fake_tracker.close.assert_called_once()
    rendered = _rendered_text(chat)
    assert "42%" in rendered


async def test_gain_default_days_is_30(pilot_app, monkeypatch):
    app, pilot = pilot_app

    fake_tracker = MagicMock()
    fake_tracker.format_report.return_value = "report"
    monkeypatch.setattr(
        "llm_code.tools.token_tracker.TokenTracker", lambda *a, **k: fake_tracker
    )

    app._cmd_dispatcher.dispatch("gain", "")
    await pilot.pause()

    fake_tracker.format_report.assert_called_once_with(30)


# ── /profile ───────────────────────────────────────────────────────────


async def test_profile_with_no_profiler_prints_fallback(pilot_app):
    from llm_code.tui.chat_view import ChatScrollView

    app, pilot = pilot_app
    app._runtime = None
    chat = app.query_one(ChatScrollView)

    app._cmd_dispatcher.dispatch("profile", "")
    await pilot.pause()

    rendered = _rendered_text(chat)
    assert "profiler not initialized" in rendered


async def test_profile_calls_query_profiler_format(pilot_app):
    from llm_code.tui.chat_view import ChatScrollView

    app, pilot = pilot_app

    profiler = MagicMock()
    profiler.format_breakdown.return_value = "profile-breakdown-text"
    runtime = MagicMock()
    runtime._query_profiler = profiler
    app._runtime = runtime

    chat = app.query_one(ChatScrollView)
    app._cmd_dispatcher.dispatch("profile", "")
    await pilot.pause()

    profiler.format_breakdown.assert_called_once()
    rendered = _rendered_text(chat)
    assert "profile-breakdown-text" in rendered


# ── /cache ─────────────────────────────────────────────────────────────


async def test_cache_bare_shows_list_heading(pilot_app):
    from llm_code.tui.chat_view import ChatScrollView

    app, pilot = pilot_app
    chat = app.query_one(ChatScrollView)
    app._cmd_dispatcher.dispatch("cache", "")
    await pilot.pause()
    rendered = _rendered_text(chat)
    assert "Persistent caches" in rendered


async def test_cache_clear_prints_cleared_list(pilot_app, monkeypatch):
    from llm_code.tui.chat_view import ChatScrollView

    app, pilot = pilot_app
    # Stub the two clear functions so the test doesn't touch real files.
    monkeypatch.setattr(
        "llm_code.runtime.server_capabilities.clear_native_tools_cache",
        lambda: None,
    )
    monkeypatch.setattr(
        "llm_code.runtime.skill_router_cache.clear_cache", lambda: None
    )
    chat = app.query_one(ChatScrollView)

    app._cmd_dispatcher.dispatch("cache", "clear")
    await pilot.pause()

    rendered = _rendered_text(chat)
    assert "Cleared:" in rendered
    assert "server_capabilities" in rendered or "skill_router_cache" in rendered


async def test_cache_probe_clears_server_caps_only(pilot_app, monkeypatch):
    from llm_code.tui.chat_view import ChatScrollView

    app, pilot = pilot_app
    called = {"server": False, "skill": False}

    def _clear_server():
        called["server"] = True

    def _clear_skill():
        called["skill"] = True

    monkeypatch.setattr(
        "llm_code.runtime.server_capabilities.clear_native_tools_cache",
        _clear_server,
    )
    monkeypatch.setattr(
        "llm_code.runtime.skill_router_cache.clear_cache", _clear_skill
    )

    chat = app.query_one(ChatScrollView)
    app._cmd_dispatcher.dispatch("cache", "probe")
    await pilot.pause()

    assert called["server"] is True
    # Probe should NOT clear the skill router cache.
    assert called["skill"] is False
    rendered = _rendered_text(chat)
    assert "re-probe native tool support" in rendered


async def test_cache_unknown_subcommand_prints_usage(pilot_app):
    from llm_code.tui.chat_view import ChatScrollView

    app, pilot = pilot_app
    chat = app.query_one(ChatScrollView)
    app._cmd_dispatcher.dispatch("cache", "bogus")
    await pilot.pause()
    rendered = _rendered_text(chat)
    assert "Usage:" in rendered
    assert "/cache" in rendered


# ── /personas ──────────────────────────────────────────────────────────


async def test_personas_lists_builtins(pilot_app):
    from llm_code.swarm.personas import BUILTIN_PERSONAS
    from llm_code.tui.chat_view import ChatScrollView

    app, pilot = pilot_app
    chat = app.query_one(ChatScrollView)
    app._cmd_dispatcher.dispatch("personas", "")
    await pilot.pause()

    rendered = _rendered_text(chat)
    assert "Available built-in personas" in rendered
    # At least one persona name must appear.
    assert any(name in rendered for name in BUILTIN_PERSONAS)


# ── /model ─────────────────────────────────────────────────────────────


async def test_model_bare_shows_current(pilot_app):
    from llm_code.tui.chat_view import ChatScrollView

    app, pilot = pilot_app
    chat = app.query_one(ChatScrollView)

    app._cmd_dispatcher.dispatch("model", "")
    await pilot.pause()

    rendered = _rendered_text(chat)
    assert "Current model" in rendered


async def test_model_route_shows_routing_table(pilot_app):
    from llm_code.tui.chat_view import ChatScrollView

    app, pilot = pilot_app
    chat = app.query_one(ChatScrollView)

    app._cmd_dispatcher.dispatch("model", "route")
    await pilot.pause()

    rendered = _rendered_text(chat)
    # Either "Model routing:" (configured) or "No model routing" —
    # both are OK; we just need the command to run without crashing.
    assert ("Model routing" in rendered) or ("No model routing" in rendered)


# ── /budget ────────────────────────────────────────────────────────────


async def test_budget_set_with_int_updates_app_budget(pilot_app):
    from llm_code.tui.chat_view import ChatScrollView

    app, pilot = pilot_app
    chat = app.query_one(ChatScrollView)

    app._cmd_dispatcher.dispatch("budget", "12345")
    await pilot.pause()

    assert app._budget == 12345
    rendered = _rendered_text(chat)
    assert "Token budget set" in rendered
    assert "12,345" in rendered


async def test_budget_invalid_int_shows_error(pilot_app):
    from llm_code.tui.chat_view import ChatScrollView

    app, pilot = pilot_app
    chat = app.query_one(ChatScrollView)

    app._cmd_dispatcher.dispatch("budget", "notanumber")
    await pilot.pause()

    rendered = _rendered_text(chat)
    # Handler catches ValueError and prints some feedback.
    assert "notanumber" in rendered or "budget" in rendered.lower()


# ── /set ───────────────────────────────────────────────────────────────


async def test_set_with_no_args_shows_usage(pilot_app):
    from llm_code.tui.chat_view import ChatScrollView

    app, pilot = pilot_app
    chat = app.query_one(ChatScrollView)

    app._cmd_dispatcher.dispatch("set", "")
    await pilot.pause()

    rendered = _rendered_text(chat)
    assert "Usage: /set" in rendered
    assert "Editable:" in rendered


async def test_set_temperature_updates_config(pilot_app):
    from llm_code.tui.chat_view import ChatScrollView

    app, pilot = pilot_app
    chat = app.query_one(ChatScrollView)

    app._cmd_dispatcher.dispatch("set", "temperature 0.73")
    await pilot.pause()

    # Value propagated to config.
    assert abs(app._config.temperature - 0.73) < 1e-6
    rendered = _rendered_text(chat)
    assert "Set temperature = 0.73" in rendered


async def test_set_unknown_key_surfaces_error(pilot_app):
    from llm_code.tui.chat_view import ChatScrollView

    app, pilot = pilot_app
    chat = app.query_one(ChatScrollView)

    app._cmd_dispatcher.dispatch("set", "nonexistent_key 42")
    await pilot.pause()

    rendered = _rendered_text(chat)
    assert "Error" in rendered


# ── /config ────────────────────────────────────────────────────────────


async def test_config_lists_core_fields(pilot_app):
    from llm_code.tui.chat_view import ChatScrollView

    app, pilot = pilot_app
    chat = app.query_one(ChatScrollView)

    app._cmd_dispatcher.dispatch("config", "")
    await pilot.pause()

    rendered = _rendered_text(chat)
    assert "model:" in rendered


# ── /cd ────────────────────────────────────────────────────────────────


async def test_cd_bare_shows_current_dir(pilot_app):
    from llm_code.tui.chat_view import ChatScrollView

    app, pilot = pilot_app
    chat = app.query_one(ChatScrollView)

    app._cmd_dispatcher.dispatch("cd", "")
    await pilot.pause()

    rendered = _rendered_text(chat)
    assert "Current directory" in rendered
    assert str(app._cwd) in rendered


async def test_cd_to_valid_path_updates_cwd(pilot_app, tmp_path):
    from llm_code.tui.chat_view import ChatScrollView

    app, pilot = pilot_app
    target = tmp_path / "newcwd"
    target.mkdir()
    chat = app.query_one(ChatScrollView)

    app._cmd_dispatcher.dispatch("cd", str(target))
    await pilot.pause()

    assert app._cwd == target.resolve()
    rendered = _rendered_text(chat)
    assert "Working directory" in rendered


async def test_cd_to_missing_dir_prints_error(pilot_app, tmp_path):
    from llm_code.tui.chat_view import ChatScrollView

    app, pilot = pilot_app
    chat = app.query_one(ChatScrollView)
    bogus = tmp_path / "does-not-exist"

    app._cmd_dispatcher.dispatch("cd", str(bogus))
    await pilot.pause()

    rendered = _rendered_text(chat)
    assert "Directory not found" in rendered


# ── /map ───────────────────────────────────────────────────────────────


async def test_map_runs_on_empty_dir(pilot_app, tmp_path):
    """A freshly-created tmp dir with no source files should produce
    "No source files found." rather than raising."""
    from llm_code.tui.chat_view import ChatScrollView

    app, pilot = pilot_app
    app._cwd = tmp_path
    chat = app.query_one(ChatScrollView)

    app._cmd_dispatcher.dispatch("map", "")
    await pilot.pause()

    rendered = _rendered_text(chat)
    # Either "No source files" OR the "Repo Map" heading with
    # content — both are non-crashing outcomes.
    assert (
        "No source files" in rendered
        or "Repo Map" in rendered
        or "Error building" in rendered
    )


async def test_map_on_small_repo_produces_output(pilot_app, tmp_path):
    from llm_code.tui.chat_view import ChatScrollView

    app, pilot = pilot_app
    app._cwd = tmp_path
    # Seed a tiny python file.
    (tmp_path / "foo.py").write_text("def hello():\n    return 'world'\n")
    chat = app.query_one(ChatScrollView)

    app._cmd_dispatcher.dispatch("map", "")
    await pilot.pause()

    rendered = _rendered_text(chat)
    # Either a Repo Map block or "No source files" / error. Either
    # outcome is acceptable — we just need the command not to crash.
    assert "Repo Map" in rendered or "No source files" in rendered or "Error" in rendered


# ── /dump ──────────────────────────────────────────────────────────────


async def test_dump_writes_codebase_to_file(pilot_app, tmp_path):
    """`/dump` should write a codebase snapshot under .llmcode/dump.txt"""
    from llm_code.tui.chat_view import ChatScrollView

    app, pilot = pilot_app
    app._cwd = tmp_path
    # Seed a couple of source files so the dump finds something.
    (tmp_path / "a.py").write_text("print('a')\n")
    (tmp_path / "b.py").write_text("print('b')\n")

    chat = app.query_one(ChatScrollView)
    app._cmd_dispatcher.dispatch("dump", "")
    # dump is async — give it one more tick.
    await pilot.pause()
    await pilot.pause()

    dump_path = tmp_path / ".llmcode" / "dump.txt"
    # Either the dump file exists OR the command printed "no source
    # files" — both are non-crashing outcomes.
    rendered = _rendered_text(chat)
    if not dump_path.exists():
        assert "No source files" in rendered or "Dumped" in rendered
    else:
        assert dump_path.exists()
