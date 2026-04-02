"""Tests for llm_code.marketplace.plugin and installer — TDD: written before implementation."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from llm_code.marketplace.plugin import InstalledPlugin, PluginManifest
from llm_code.marketplace.installer import PluginInstaller


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_plugin_dir(tmp_path: Path, data: dict, name: str = "my-plugin") -> Path:
    """Create a plugin directory with .claude-plugin/plugin.json."""
    plugin_dir = tmp_path / name
    manifest_dir = plugin_dir / ".claude-plugin"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "plugin.json").write_text(json.dumps(data))
    return plugin_dir


# ---------------------------------------------------------------------------
# PluginManifest
# ---------------------------------------------------------------------------

class TestPluginManifest:
    def test_from_path_basic(self, tmp_path: Path):
        data = {
            "name": "my-plugin",
            "version": "1.0.0",
            "description": "A test plugin",
        }
        plugin_dir = make_plugin_dir(tmp_path, data)
        manifest = PluginManifest.from_path(plugin_dir)
        assert manifest.name == "my-plugin"
        assert manifest.version == "1.0.0"
        assert manifest.description == "A test plugin"

    def test_from_path_with_mcp_servers(self, tmp_path: Path):
        data = {
            "name": "mcp-plugin",
            "version": "2.0.0",
            "description": "Plugin with MCP",
            "mcpServers": [{"name": "server1", "command": "npx", "args": ["server1"]}],
        }
        plugin_dir = make_plugin_dir(tmp_path, data, name="mcp-plugin")
        manifest = PluginManifest.from_path(plugin_dir)
        assert manifest.name == "mcp-plugin"
        assert manifest.mcp_servers is not None
        assert len(manifest.mcp_servers) == 1
        assert manifest.mcp_servers[0]["name"] == "server1"

    def test_from_path_with_skills_field(self, tmp_path: Path):
        data = {
            "name": "skill-plugin",
            "version": "0.1.0",
            "description": "Plugin with skills",
            "skills": ["skill-a", "skill-b"],
        }
        plugin_dir = make_plugin_dir(tmp_path, data, name="skill-plugin")
        manifest = PluginManifest.from_path(plugin_dir)
        assert manifest.skills is not None
        assert "skill-a" in manifest.skills

    def test_from_path_missing_dir_raises(self, tmp_path: Path):
        missing = tmp_path / "nonexistent-plugin"
        with pytest.raises(FileNotFoundError):
            PluginManifest.from_path(missing)

    def test_from_path_no_manifest_raises(self, tmp_path: Path):
        plugin_dir = tmp_path / "empty-plugin"
        plugin_dir.mkdir()
        with pytest.raises(FileNotFoundError):
            PluginManifest.from_path(plugin_dir)

    def test_manifest_is_frozen(self, tmp_path: Path):
        import dataclasses
        data = {"name": "x", "version": "1.0.0", "description": "x"}
        plugin_dir = make_plugin_dir(tmp_path, data, name="x")
        manifest = PluginManifest.from_path(plugin_dir)
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            manifest.name = "mutated"  # type: ignore[misc]

    def test_from_path_full_fields(self, tmp_path: Path):
        data = {
            "name": "full-plugin",
            "version": "3.0.0",
            "description": "Full featured plugin",
            "author": {"name": "Test Author", "email": "author@example.com"},
            "homepage": "https://example.com",
            "repository": "https://github.com/example/full-plugin",
            "keywords": ["keyword1", "keyword2"],
            "commands": ["cmd1", "cmd2"],
            "agents": ["agent1"],
            "hooks": {"pre": "hook.sh"},
            "lspServers": [{"name": "lsp1"}],
        }
        plugin_dir = make_plugin_dir(tmp_path, data, name="full-plugin")
        manifest = PluginManifest.from_path(plugin_dir)
        assert manifest.author == {"name": "Test Author", "email": "author@example.com"}
        assert manifest.homepage == "https://example.com"
        assert manifest.repository == "https://github.com/example/full-plugin"
        assert "keyword1" in manifest.keywords
        assert "cmd1" in manifest.commands
        assert manifest.agents is not None
        assert manifest.lsp_servers is not None


# ---------------------------------------------------------------------------
# InstalledPlugin
# ---------------------------------------------------------------------------

class TestInstalledPlugin:
    def test_installed_plugin_frozen(self, tmp_path: Path):
        import dataclasses
        data = {"name": "p", "version": "1.0.0", "description": "p"}
        plugin_dir = make_plugin_dir(tmp_path, data, name="p")
        manifest = PluginManifest.from_path(plugin_dir)
        ip = InstalledPlugin(manifest=manifest, path=plugin_dir, enabled=True)
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            ip.enabled = False  # type: ignore[misc]

    def test_installed_plugin_defaults(self, tmp_path: Path):
        data = {"name": "p2", "version": "1.0.0", "description": "p2"}
        plugin_dir = make_plugin_dir(tmp_path, data, name="p2")
        manifest = PluginManifest.from_path(plugin_dir)
        ip = InstalledPlugin(manifest=manifest, path=plugin_dir, enabled=True)
        assert ip.scope == "user"
        assert ip.installed_from == "local"


# ---------------------------------------------------------------------------
# PluginInstaller
# ---------------------------------------------------------------------------

class TestPluginInstaller:
    def test_install_from_local(self, tmp_path: Path):
        install_dir = tmp_path / "installed"
        source_dir = tmp_path / "source"
        data = {"name": "test-plugin", "version": "1.0.0", "description": "Test"}
        make_plugin_dir(source_dir, data, name="")
        # source_dir itself is the plugin dir (has .claude-plugin/)
        (source_dir / ".claude-plugin" / "plugin.json").write_text(json.dumps(data))

        installer = PluginInstaller(install_dir)
        result_path = installer.install_from_local(source_dir)

        assert result_path.exists()
        assert (result_path / ".claude-plugin" / "plugin.json").exists()

    def test_list_installed_with_state(self, tmp_path: Path):
        install_dir = tmp_path / "installed"
        data = {"name": "listed-plugin", "version": "1.0.0", "description": "Listed"}
        source_dir = tmp_path / "src"
        source_dir.mkdir()
        manifest_dir = source_dir / ".claude-plugin"
        manifest_dir.mkdir()
        (manifest_dir / "plugin.json").write_text(json.dumps(data))

        installer = PluginInstaller(install_dir)
        installer.install_from_local(source_dir)

        plugins = installer.list_installed()
        assert len(plugins) == 1
        assert plugins[0].manifest.name == "listed-plugin"
        assert plugins[0].enabled is True

    def test_enable_disable(self, tmp_path: Path):
        install_dir = tmp_path / "installed"
        data = {"name": "toggle-plugin", "version": "1.0.0", "description": "Toggle"}
        source_dir = tmp_path / "src"
        source_dir.mkdir()
        manifest_dir = source_dir / ".claude-plugin"
        manifest_dir.mkdir()
        (manifest_dir / "plugin.json").write_text(json.dumps(data))

        installer = PluginInstaller(install_dir)
        installer.install_from_local(source_dir)

        installer.disable("toggle-plugin")
        plugins = installer.list_installed()
        assert plugins[0].enabled is False

        installer.enable("toggle-plugin")
        plugins = installer.list_installed()
        assert plugins[0].enabled is True

    def test_uninstall_removes_dir(self, tmp_path: Path):
        install_dir = tmp_path / "installed"
        data = {"name": "removable", "version": "1.0.0", "description": "Remove me"}
        source_dir = tmp_path / "src"
        source_dir.mkdir()
        manifest_dir = source_dir / ".claude-plugin"
        manifest_dir.mkdir()
        (manifest_dir / "plugin.json").write_text(json.dumps(data))

        installer = PluginInstaller(install_dir)
        installed_path = installer.install_from_local(source_dir)

        assert installed_path.exists()
        installer.uninstall("removable")
        assert not installed_path.exists()

        plugins = installer.list_installed()
        assert len(plugins) == 0
