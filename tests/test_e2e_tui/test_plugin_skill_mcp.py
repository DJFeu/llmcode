"""E2E: 外掛生態系 — /plugin, /skill, /mcp round-trip tests.

These commands touch the filesystem, subprocess (git clone), and
sometimes the network. Every test here stubs the I/O layer so the
tests run deterministically without touching ~/.llmcode or cloning
real repos, while still exercising the command-layer decision tree
(install / enable / disable / remove / list browser).
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from tests.test_e2e_tui.test_boot_banner import _rendered_text


# ── /mcp ───────────────────────────────────────────────────────────────


async def test_mcp_install_writes_to_config_json(
    pilot_app, tmp_path, monkeypatch
):
    """`/mcp install @scope/pkg` should append to mcp_servers in
    ~/.llmcode/config.json and print a confirmation."""
    import dataclasses

    from llm_code.runtime.config import RuntimeConfig
    from llm_code.tui.chat_view import ChatScrollView

    app, pilot = pilot_app
    # Redirect ~/.llmcode to tmp_path so we don't touch real config.
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    # Seed a minimal config so the merge path has a starting point.
    cfg_dir = tmp_path / ".llmcode"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "config.json").write_text(json.dumps({"model": "test"}))
    # Inject a RuntimeConfig (the fixture stub doesn't ship one by
    # default for the mcp_servers field).
    app._config = dataclasses.replace(RuntimeConfig(), mcp_servers={})
    # Stub the hot-start so we don't actually spawn npx.
    app._hot_start_mcp = MagicMock()  # type: ignore[method-assign]

    chat = app.query_one(ChatScrollView)
    app._cmd_dispatcher.dispatch("mcp", "install @anthropic/mcp-server-fs")
    await pilot.pause()

    # Config written on disk.
    data = json.loads((cfg_dir / "config.json").read_text())
    assert "mcp_servers" in data
    assert "mcp-server-fs" in data["mcp_servers"]
    assert data["mcp_servers"]["mcp-server-fs"]["command"] == "npx"
    # Hot-start called.
    app._hot_start_mcp.assert_called_once()

    rendered = _rendered_text(chat)
    assert "Added mcp-server-fs" in rendered


async def test_mcp_remove_strips_server_from_config(
    pilot_app, tmp_path, monkeypatch
):
    import dataclasses

    from llm_code.runtime.config import RuntimeConfig
    from llm_code.tui.chat_view import ChatScrollView

    app, pilot = pilot_app
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    cfg_dir = tmp_path / ".llmcode"
    cfg_dir.mkdir(parents=True)
    # Pre-populate with a server the user wants to remove.
    (cfg_dir / "config.json").write_text(
        json.dumps(
            {
                "mcp_servers": {
                    "existing-srv": {"command": "npx", "args": ["-y", "foo"]},
                }
            }
        )
    )
    app._config = dataclasses.replace(
        RuntimeConfig(),
        mcp_servers={"existing-srv": {"command": "npx", "args": ["-y", "foo"]}},
    )

    chat = app.query_one(ChatScrollView)
    app._cmd_dispatcher.dispatch("mcp", "remove existing-srv")
    await pilot.pause()

    data = json.loads((cfg_dir / "config.json").read_text())
    assert "existing-srv" not in data.get("mcp_servers", {})

    rendered = _rendered_text(chat)
    assert "Removed existing-srv" in rendered


async def test_mcp_remove_missing_prints_not_found(
    pilot_app, tmp_path, monkeypatch
):
    import dataclasses

    from llm_code.runtime.config import RuntimeConfig
    from llm_code.tui.chat_view import ChatScrollView

    app, pilot = pilot_app
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    cfg_dir = tmp_path / ".llmcode"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "config.json").write_text(json.dumps({"mcp_servers": {}}))
    app._config = dataclasses.replace(RuntimeConfig(), mcp_servers={})

    chat = app.query_one(ChatScrollView)
    app._cmd_dispatcher.dispatch("mcp", "remove does-not-exist")
    await pilot.pause()

    rendered = _rendered_text(chat)
    assert "not found" in rendered


async def test_mcp_bare_pushes_marketplace_browser(pilot_app, tmp_path, monkeypatch):
    """Bare `/mcp` should push a MarketplaceBrowser modal. We patch
    push_screen instead of asserting on the real widget tree because
    MarketplaceBrowser's on_mount schedules a render that races with
    Textual's pilot — the widget query raises NoMatches if inspected
    before the next tick, and forcing an extra pause runs into
    asyncio shutdown oddities."""
    import dataclasses

    from llm_code.runtime.config import RuntimeConfig

    app, pilot = pilot_app
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    app._config = dataclasses.replace(
        RuntimeConfig(),
        mcp_servers={"context7": {"command": "npx", "args": ["-y", "context7-mcp"]}},
    )

    pushed = {"screen": None}

    def _track(screen, *args, **kwargs):
        pushed["screen"] = screen
        return None  # don't actually mount; avoid the render race

    app.push_screen = _track  # type: ignore[method-assign]

    app._cmd_dispatcher.dispatch("mcp", "")
    await pilot.pause()

    assert pushed["screen"] is not None
    assert pushed["screen"].__class__.__name__ == "MarketplaceBrowser"


# ── /skill ─────────────────────────────────────────────────────────────


async def test_skill_install_rejects_invalid_repo(pilot_app):
    from llm_code.tui.chat_view import ChatScrollView

    app, pilot = pilot_app
    chat = app.query_one(ChatScrollView)

    app._cmd_dispatcher.dispatch("skill", "install not-a-valid-repo-format")
    await pilot.pause()

    rendered = _rendered_text(chat)
    assert "Usage: /skill install" in rendered


async def test_skill_install_clones_and_reloads(pilot_app, tmp_path, monkeypatch):
    """A valid `owner/repo` path should git-clone to tmp, copy into
    ~/.llmcode/skills/<name>, and call app._reload_skills."""
    from llm_code.tui.chat_view import ChatScrollView

    app, pilot = pilot_app
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    skills_dir = tmp_path / ".llmcode" / "skills"
    skills_dir.mkdir(parents=True)

    # Patch subprocess.run so git clone seeds a fake repo.
    cloned = {"called": False}

    def _fake_clone(cmd, *args, **kwargs):
        cloned["called"] = True
        # Last arg is the destination temp directory.
        tmp_clone = Path(cmd[-1])
        # Simulate the repo having a top-level "skills" folder
        # with one skill marker file inside.
        (tmp_clone / "skills").mkdir(parents=True, exist_ok=True)
        (tmp_clone / "skills" / "example.md").write_text("# example skill\n")
        result = MagicMock()
        result.returncode = 0
        result.stderr = ""
        return result

    monkeypatch.setattr(
        "llm_code.tui.command_dispatcher.subprocess.run", _fake_clone
    )

    reload_called = {"n": 0}

    def _reload_skills():
        reload_called["n"] += 1

    app._reload_skills = _reload_skills  # type: ignore[method-assign]

    chat = app.query_one(ChatScrollView)
    app._cmd_dispatcher.dispatch("skill", "install obra/superpowers")
    await pilot.pause()

    assert cloned["called"] is True
    assert (skills_dir / "superpowers" / "example.md").exists()
    assert reload_called["n"] == 1

    rendered = _rendered_text(chat)
    assert "Installed superpowers" in rendered
    assert "Activated" in rendered


async def test_skill_disable_creates_marker_and_reloads(
    pilot_app, tmp_path, monkeypatch
):
    from llm_code.tui.chat_view import ChatScrollView

    app, pilot = pilot_app
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    skill_dir = tmp_path / ".llmcode" / "skills" / "test-skill"
    skill_dir.mkdir(parents=True)

    reload_called = {"n": 0}
    app._reload_skills = lambda: reload_called.update(n=reload_called["n"] + 1)  # type: ignore

    chat = app.query_one(ChatScrollView)
    app._cmd_dispatcher.dispatch("skill", "disable test-skill")
    await pilot.pause()

    assert (skill_dir / ".disabled").exists()
    assert reload_called["n"] == 1
    rendered = _rendered_text(chat)
    assert "Disabled test-skill" in rendered


async def test_skill_enable_removes_marker_and_reloads(
    pilot_app, tmp_path, monkeypatch
):
    from llm_code.tui.chat_view import ChatScrollView

    app, pilot = pilot_app
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    skill_dir = tmp_path / ".llmcode" / "skills" / "already-disabled"
    skill_dir.mkdir(parents=True)
    # Start with a disabled marker.
    (skill_dir / ".disabled").touch()

    reload_called = {"n": 0}
    app._reload_skills = lambda: reload_called.update(n=reload_called["n"] + 1)  # type: ignore

    chat = app.query_one(ChatScrollView)
    app._cmd_dispatcher.dispatch("skill", "enable already-disabled")
    await pilot.pause()

    assert not (skill_dir / ".disabled").exists()
    assert reload_called["n"] == 1
    rendered = _rendered_text(chat)
    assert "Enabled already-disabled" in rendered


async def test_skill_remove_deletes_dir(pilot_app, tmp_path, monkeypatch):
    from llm_code.tui.chat_view import ChatScrollView

    app, pilot = pilot_app
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    skill_dir = tmp_path / ".llmcode" / "skills" / "doomed-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "some-file.md").write_text("x")

    app._reload_skills = lambda: None  # type: ignore

    chat = app.query_one(ChatScrollView)
    app._cmd_dispatcher.dispatch("skill", "remove doomed-skill")
    await pilot.pause()

    assert not skill_dir.exists()
    rendered = _rendered_text(chat)
    assert "Removed doomed-skill" in rendered


async def test_skill_remove_missing_prints_not_found(
    pilot_app, tmp_path, monkeypatch
):
    from llm_code.tui.chat_view import ChatScrollView

    app, pilot = pilot_app
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    chat = app.query_one(ChatScrollView)
    app._cmd_dispatcher.dispatch("skill", "remove does-not-exist")
    await pilot.pause()

    rendered = _rendered_text(chat)
    assert "Not found" in rendered


async def test_skill_install_rejects_unsafe_name(pilot_app):
    """The `_is_safe_name` guard should reject names with path
    traversal characters or special tokens."""
    from llm_code.tui.chat_view import ChatScrollView

    app, pilot = pilot_app
    chat = app.query_one(ChatScrollView)

    app._cmd_dispatcher.dispatch("skill", "enable ../../etc/passwd")
    await pilot.pause()

    rendered = _rendered_text(chat)
    assert "Invalid skill name" in rendered


async def test_skill_bare_pushes_browser(pilot_app, tmp_path, monkeypatch):
    """`/skill` with no args should push a Skills Marketplace browser.

    Same note as the MCP browser test: we patch push_screen rather
    than let the modal actually mount, to avoid racing against
    MarketplaceBrowser's internal render scheduler in pilot mode.
    """
    app, pilot = pilot_app
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    pushed = {"screen": None}

    def _track(screen, *args, **kwargs):
        pushed["screen"] = screen
        return None

    app.push_screen = _track  # type: ignore[method-assign]

    app._cmd_dispatcher.dispatch("skill", "")
    await pilot.pause()

    assert pushed["screen"] is not None
    assert pushed["screen"].__class__.__name__ == "MarketplaceBrowser"


# ── /plugin ────────────────────────────────────────────────────────────


async def test_plugin_install_rejects_invalid_repo(pilot_app):
    from llm_code.tui.chat_view import ChatScrollView

    app, pilot = pilot_app
    chat = app.query_one(ChatScrollView)

    app._cmd_dispatcher.dispatch("plugin", "install garbage")
    await pilot.pause()

    rendered = _rendered_text(chat)
    assert "Usage: /plugin install" in rendered


async def test_plugin_install_clones_and_enables(
    pilot_app, tmp_path, monkeypatch
):
    """A valid install should clone, enable via PluginInstaller, and
    call _reload_skills + _load_plugin_tools."""
    from llm_code.tui.chat_view import ChatScrollView

    app, pilot = pilot_app
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    plugins_dir = tmp_path / ".llmcode" / "plugins"
    plugins_dir.mkdir(parents=True)

    # Fake PluginInstaller — we just need .enable to be observable.
    fake_installer = MagicMock()

    def _ctor(*args, **kwargs):
        return fake_installer

    monkeypatch.setattr(
        "llm_code.marketplace.installer.PluginInstaller", _ctor
    )

    def _fake_clone(cmd, *args, **kwargs):
        # Last arg is destination (a real path, not tmpdir).
        dest = Path(cmd[-1])
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "plugin.json").write_text("{}")
        return MagicMock(returncode=0, stderr="")

    monkeypatch.setattr(
        "llm_code.tui.command_dispatcher.subprocess.run", _fake_clone
    )

    reload_called = {"n": 0}
    app._reload_skills = lambda: reload_called.update(n=reload_called["n"] + 1)  # type: ignore
    tools_loaded = {"n": 0}
    app._load_plugin_tools = lambda dest, chat: tools_loaded.update(  # type: ignore
        n=tools_loaded["n"] + 1
    )

    chat = app.query_one(ChatScrollView)
    app._cmd_dispatcher.dispatch("plugin", "install obra/superpowers")
    await pilot.pause()

    fake_installer.enable.assert_called_once_with("superpowers")
    assert reload_called["n"] == 1
    assert tools_loaded["n"] == 1
    rendered = _rendered_text(chat)
    assert "Installed superpowers" in rendered


async def test_plugin_enable_calls_installer_enable(
    pilot_app, tmp_path, monkeypatch
):
    from llm_code.tui.chat_view import ChatScrollView

    app, pilot = pilot_app
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    fake_installer = MagicMock()
    monkeypatch.setattr(
        "llm_code.marketplace.installer.PluginInstaller",
        lambda *a, **k: fake_installer,
    )

    app._reload_skills = lambda: None  # type: ignore
    app._load_plugin_tools = lambda *a, **k: None  # type: ignore

    chat = app.query_one(ChatScrollView)
    app._cmd_dispatcher.dispatch("plugin", "enable my-plugin")
    await pilot.pause()

    fake_installer.enable.assert_called_once_with("my-plugin")
    rendered = _rendered_text(chat)
    assert "Enabled my-plugin" in rendered


async def test_plugin_disable_calls_installer_disable(
    pilot_app, tmp_path, monkeypatch
):
    from llm_code.tui.chat_view import ChatScrollView

    app, pilot = pilot_app
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    fake_installer = MagicMock()
    monkeypatch.setattr(
        "llm_code.marketplace.installer.PluginInstaller",
        lambda *a, **k: fake_installer,
    )
    app._reload_skills = lambda: None  # type: ignore
    app._unload_plugin_tools = lambda *a, **k: None  # type: ignore

    chat = app.query_one(ChatScrollView)
    app._cmd_dispatcher.dispatch("plugin", "disable my-plugin")
    await pilot.pause()

    fake_installer.disable.assert_called_once_with("my-plugin")
    rendered = _rendered_text(chat)
    assert "Disabled my-plugin" in rendered


async def test_plugin_rejects_unsafe_name(pilot_app, tmp_path, monkeypatch):
    from llm_code.tui.chat_view import ChatScrollView

    app, pilot = pilot_app
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    # Stub the installer so the import guard still passes.
    monkeypatch.setattr(
        "llm_code.marketplace.installer.PluginInstaller",
        lambda *a, **k: MagicMock(),
    )

    chat = app.query_one(ChatScrollView)
    app._cmd_dispatcher.dispatch("plugin", "enable ../../secret")
    await pilot.pause()

    rendered = _rendered_text(chat)
    assert "Invalid plugin name" in rendered
