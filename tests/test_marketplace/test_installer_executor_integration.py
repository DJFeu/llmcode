"""End-to-end installer + executor integration tests (v16 M3).

The v2.5.5 ``/plugin install`` slash command bypassed the security
scanner and the executor. v16 M3 routes both through the marketplace
modules; these tests pin the wiring so a future refactor can't drop
the security pass or the dynamic tool registration silently.
"""
from __future__ import annotations

import json
import shutil
import textwrap
from pathlib import Path

import pytest

from llm_code.marketplace.executor import (
    PluginConflictError,
    load_plugin,
    unload_plugin,
)
from llm_code.marketplace.installer import (
    PluginInstaller,
    SecurityScanError,
)
from llm_code.marketplace.plugin import PluginManifest
from llm_code.tools.registry import ToolRegistry


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tool_registry() -> ToolRegistry:
    return ToolRegistry()


@pytest.fixture
def install_dir(tmp_path: Path) -> Path:
    d = tmp_path / "install_root"
    d.mkdir()
    return d


@pytest.fixture
def installer(install_dir: Path) -> PluginInstaller:
    return PluginInstaller(install_dir)


def _make_plugin(
    root: Path,
    name: str = "demo-plugin",
    version: str = "1.0.0",
    description: str = "Demo plugin",
    *,
    tool_module: str | None = None,
    tool_class: str | None = "EchoTool",
    permissions: dict | None = None,
) -> Path:
    """Lay down a Claude-Code-shaped plugin tree under *root*."""
    plugin_dir = root / name
    plugin_dir.mkdir(parents=True, exist_ok=True)
    meta_dir = plugin_dir / ".claude-plugin"
    meta_dir.mkdir(exist_ok=True)
    manifest: dict = {
        "name": name,
        "version": version,
        "description": description,
    }
    if tool_module:
        manifest["providesTools"] = [f"{tool_module}:{tool_class}"]
        # Drop a tiny tool module into the plugin tree so the executor
        # can import it after sys.path injection.
        (plugin_dir / f"{tool_module}.py").write_text(
            textwrap.dedent(
                f"""
                from llm_code.tools.base import PermissionLevel, Tool, ToolResult


                class {tool_class}(Tool):
                    @property
                    def name(self) -> str:
                        return "{name}_echo"

                    @property
                    def description(self) -> str:
                        return "Echo input back."

                    @property
                    def input_schema(self) -> dict:
                        return {{
                            "type": "object",
                            "properties": {{"text": {{"type": "string"}}}},
                            "required": ["text"],
                        }}

                    @property
                    def required_permission(self) -> PermissionLevel:
                        return PermissionLevel.READ_ONLY

                    def is_concurrency_safe(self, args: dict) -> bool:
                        return True

                    def execute(self, args: dict) -> ToolResult:
                        return ToolResult(output=args.get("text", ""))
                """
            ).lstrip(),
            encoding="utf-8",
        )
    if permissions is not None:
        manifest["permissions"] = permissions
    (meta_dir / "plugin.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    return plugin_dir


# ---------------------------------------------------------------------------
# install_from_local + executor
# ---------------------------------------------------------------------------


class TestInstallerExecutorWiring:
    def test_install_then_load_registers_tool(
        self,
        tmp_path: Path,
        installer: PluginInstaller,
        tool_registry: ToolRegistry,
    ) -> None:
        src = _make_plugin(
            tmp_path / "src",
            name="echoer",
            tool_module="echoer_module",
        )
        dest = installer.install_from_local(src)
        manifest = PluginManifest.from_path(dest)
        handle = load_plugin(
            manifest, dest, tool_registry=tool_registry,
        )
        try:
            assert "echoer_echo" in {t.name for t in tool_registry.all_tools()}
            tool = tool_registry.get("echoer_echo")
            assert tool is not None
            result = tool.execute({"text": "hi"})
            assert result.output == "hi"
        finally:
            unload_plugin(handle, tool_registry=tool_registry)
        assert tool_registry.get("echoer_echo") is None

    def test_install_passes_security_scan(
        self,
        tmp_path: Path,
        installer: PluginInstaller,
    ) -> None:
        src = _make_plugin(tmp_path / "src", name="clean-plugin")
        # Should not raise — no secrets, no postinstall.
        installer.install_from_local(src)

    def test_install_blocks_secret(
        self,
        tmp_path: Path,
        installer: PluginInstaller,
    ) -> None:
        src = _make_plugin(tmp_path / "src", name="leaky")
        # Drop a fake AWS key into the plugin tree.
        (src / "config.txt").write_text(
            "AKIAIOSFODNN7EXAMPLE  AWS access key example\n"
            "secret = AKIAIOSFODNN7EXAMPLE\n",
            encoding="utf-8",
        )
        with pytest.raises(SecurityScanError):
            installer.install_from_local(src)


# ---------------------------------------------------------------------------
# Conflict handling
# ---------------------------------------------------------------------------


class TestExecutorConflicts:
    def test_two_plugins_same_tool_name_collide(
        self,
        tmp_path: Path,
        installer: PluginInstaller,
        tool_registry: ToolRegistry,
    ) -> None:
        # Two plugins, both expose a tool — but the test plugin's tool
        # name is namespaced as "<plugin_name>_echo", so they don't
        # actually collide. Force a collision by giving them the same
        # tool name via a custom module.
        src1 = _make_plugin(
            tmp_path / "src1", name="alpha", tool_module="alpha_mod",
        )
        src2 = _make_plugin(
            tmp_path / "src2", name="alpha", tool_module="alpha_mod",
        )
        # Replace the second plugin's tool-name implementation so it
        # returns the same name as the first.
        (src2 / "alpha_mod.py").write_text(
            textwrap.dedent(
                """
                from llm_code.tools.base import PermissionLevel, Tool, ToolResult


                class EchoTool(Tool):
                    @property
                    def name(self) -> str:
                        return "alpha_echo"
                    @property
                    def description(self) -> str:
                        return "Same name as alpha."
                    @property
                    def input_schema(self) -> dict:
                        return {"type": "object"}
                    @property
                    def required_permission(self) -> PermissionLevel:
                        return PermissionLevel.READ_ONLY
                    def is_concurrency_safe(self, args: dict) -> bool:
                        return True
                    def execute(self, args: dict) -> ToolResult:
                        return ToolResult(output="x")
                """
            ).lstrip(),
            encoding="utf-8",
        )
        # Both plugins install to the same destination because the
        # manifest.name is identical. The second call simply overwrites
        # the first. To test executor conflict, we load both manifests
        # against a single registry.
        dest1 = installer.install_from_local(src1)
        m1 = PluginManifest.from_path(dest1)
        h1 = load_plugin(m1, dest1, tool_registry=tool_registry)
        try:
            # Fresh manifest from src2 (different on-disk install) —
            # explicitly point the executor at it instead of dest1.
            shutil.copytree(src2, tmp_path / "alt", dirs_exist_ok=True)
            m2 = PluginManifest.from_path(tmp_path / "alt")
            with pytest.raises(PluginConflictError):
                load_plugin(m2, tmp_path / "alt", tool_registry=tool_registry)
            # Registry is back to its post-h1 state — no half-load.
            assert {t.name for t in tool_registry.all_tools()} == {"alpha_echo"}
        finally:
            unload_plugin(h1, tool_registry=tool_registry)


# ---------------------------------------------------------------------------
# Subdir-bearing manifest (read-only mock — install_from_github not exercised)
# ---------------------------------------------------------------------------


class TestSubdirManifest:
    def test_manifest_loads_from_arbitrary_subdir(
        self, tmp_path: Path
    ) -> None:
        # Layout: registry/plugins/foo/bar/plugin.json
        sub = tmp_path / "registry" / "plugins" / "foo" / "bar"
        meta = sub / ".claude-plugin"
        meta.mkdir(parents=True)
        (meta / "plugin.json").write_text(
            json.dumps({"name": "foo-bar", "version": "1.0.0"}),
            encoding="utf-8",
        )
        manifest = PluginManifest.from_path(sub)
        assert manifest.name == "foo-bar"
        assert manifest.version == "1.0.0"


# ---------------------------------------------------------------------------
# Permissions gate
# ---------------------------------------------------------------------------


class TestExecutorPermissions:
    def test_dangerous_capability_blocked_without_force(
        self,
        tmp_path: Path,
        tool_registry: ToolRegistry,
    ) -> None:
        from llm_code.marketplace.executor import PluginLoadError

        plugin_dir = _make_plugin(
            tmp_path,
            name="risky",
            tool_module="risky_mod",
            permissions={"subprocess": True},
        )
        manifest = PluginManifest.from_path(plugin_dir)
        with pytest.raises(PluginLoadError) as exc_info:
            load_plugin(manifest, plugin_dir, tool_registry=tool_registry)
        assert "subprocess" in str(exc_info.value)
        # Registry untouched — no half-load.
        assert tool_registry.all_tools() == ()

    def test_dangerous_capability_loads_with_force(
        self,
        tmp_path: Path,
        tool_registry: ToolRegistry,
    ) -> None:
        plugin_dir = _make_plugin(
            tmp_path,
            name="risky",
            tool_module="risky_mod",
            permissions={"subprocess": True},
        )
        manifest = PluginManifest.from_path(plugin_dir)
        handle = load_plugin(
            manifest, plugin_dir, tool_registry=tool_registry, force=True,
        )
        try:
            assert tool_registry.get("risky_echo") is not None
        finally:
            unload_plugin(handle, tool_registry=tool_registry)
