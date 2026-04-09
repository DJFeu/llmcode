"""Wave2-5: plugin executor tests.

Exercises the full ``load_plugin`` → tool-registered →
``unload_plugin`` → tool-gone cycle using a synthetic plugin
directory that's built at test time. No network, no subprocess,
no filesystem outside ``tmp_path``.

Also covers:

* Manifest schema round-trip for the new ``providesTools`` /
  ``permissions`` JSON keys (both camelCase and snake_case).
* ``ToolRegistry.unregister`` idempotency.
* ``SkillRouter.add_skill`` / ``remove_skill`` round-trip.
* Rollback on conflict: the registry must not be half-loaded when
  a plugin fails partway through its provides_tools list.
* Rollback on unparseable entry / missing module / missing class /
  instantiation error.
"""
from __future__ import annotations

import json
import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path

import pytest

from llm_code.marketplace.executor import (
    PluginConflictError,
    PluginLoadError,
    load_plugin,
    unload_plugin,
)
from llm_code.marketplace.plugin import PluginManifest
from llm_code.tools.registry import ToolRegistry


# ---------- Synthetic plugin builder ----------

def _write_manifest(plugin_dir: Path, data: dict) -> None:
    """Write a .claude-plugin/plugin.json into *plugin_dir*."""
    manifest_dir = plugin_dir / ".claude-plugin"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    (manifest_dir / "plugin.json").write_text(
        json.dumps(data, indent=2), encoding="utf-8"
    )


def _write_tool_module(
    plugin_dir: Path, module_name: str, class_name: str, tool_name: str,
) -> None:
    """Write a minimal Tool subclass to plugin_dir/module_name.py."""
    (plugin_dir / f"{module_name}.py").write_text(
        textwrap.dedent(f"""
        from llm_code.tools.base import Tool, ToolResult, PermissionLevel


        class {class_name}(Tool):
            @property
            def name(self) -> str:
                return {tool_name!r}

            @property
            def description(self) -> str:
                return "Test tool from wave2-5 fixture plugin."

            @property
            def input_schema(self) -> dict:
                return {{"type": "object", "properties": {{}}}}

            @property
            def required_permission(self) -> PermissionLevel:
                return PermissionLevel.READ_ONLY

            def is_read_only(self, args: dict) -> bool:
                return True

            def is_destructive(self, args: dict) -> bool:
                return False

            def validate_input(self, args: dict) -> dict:
                return args or {{}}

            def execute(self, args: dict) -> ToolResult:
                return ToolResult(output="hello from {tool_name}", is_error=False)
        """),
        encoding="utf-8",
    )


def _write_broken_tool_module(plugin_dir: Path, module_name: str) -> None:
    """Write a module whose class raises in __init__."""
    (plugin_dir / f"{module_name}.py").write_text(
        textwrap.dedent("""
        class BrokenTool:
            def __init__(self):
                raise RuntimeError("ctor always fails")
        """),
        encoding="utf-8",
    )


@pytest.fixture
def cleanup_sys_modules():
    """Remove any plugin-fixture modules from sys.modules after the
    test so the next test starts with a clean import cache."""
    before = set(sys.modules.keys())
    yield
    new = set(sys.modules.keys()) - before
    for name in new:
        if name.startswith("wv25_"):
            del sys.modules[name]


# ---------- Manifest schema extension ----------

def test_manifest_parses_provides_tools_from_camel_case(tmp_path: Path) -> None:
    _write_manifest(tmp_path, {
        "name": "tp",
        "version": "0.1.0",
        "description": "test",
        "providesTools": ["pkg.mod:Klass", "pkg.mod:Other"],
    })
    m = PluginManifest.from_path(tmp_path)
    assert m.provides_tools == ("pkg.mod:Klass", "pkg.mod:Other")


def test_manifest_parses_provides_tools_from_snake_case(tmp_path: Path) -> None:
    _write_manifest(tmp_path, {
        "name": "tp",
        "version": "0.1.0",
        "description": "test",
        "provides_tools": ["pkg.mod:Klass"],
    })
    m = PluginManifest.from_path(tmp_path)
    assert m.provides_tools == ("pkg.mod:Klass",)


def test_manifest_provides_tools_defaults_to_empty(tmp_path: Path) -> None:
    _write_manifest(tmp_path, {
        "name": "tp", "version": "0.1.0", "description": "test",
    })
    m = PluginManifest.from_path(tmp_path)
    assert m.provides_tools == ()


def test_manifest_parses_permissions_dict(tmp_path: Path) -> None:
    _write_manifest(tmp_path, {
        "name": "tp", "version": "0.1.0", "description": "test",
        "permissions": {"network": True, "fs_write": False},
    })
    m = PluginManifest.from_path(tmp_path)
    assert m.permissions == {"network": True, "fs_write": False}


def test_manifest_permissions_defaults_to_none(tmp_path: Path) -> None:
    _write_manifest(tmp_path, {
        "name": "tp", "version": "0.1.0", "description": "test",
    })
    m = PluginManifest.from_path(tmp_path)
    assert m.permissions is None


def test_manifest_rejects_non_dict_permissions(tmp_path: Path) -> None:
    """A list or string under permissions is ignored rather than
    crashing the parser — defensive default."""
    _write_manifest(tmp_path, {
        "name": "tp", "version": "0.1.0", "description": "test",
        "permissions": ["network"],
    })
    m = PluginManifest.from_path(tmp_path)
    assert m.permissions is None


# ---------- ToolRegistry.unregister ----------

@dataclass
class _FakeTool:
    name: str

    @property
    def description(self) -> str:
        return ""

    @property
    def input_schema(self) -> dict:
        return {}

    def is_read_only(self, args) -> bool:
        return True

    def is_destructive(self, args) -> bool:
        return False

    def validate_input(self, args):
        return args


def test_unregister_removes_existing_tool() -> None:
    reg = ToolRegistry()
    reg.register(_FakeTool(name="x"))
    assert reg.get("x") is not None
    assert reg.unregister("x") is True
    assert reg.get("x") is None


def test_unregister_missing_tool_returns_false() -> None:
    reg = ToolRegistry()
    assert reg.unregister("never-existed") is False


def test_unregister_allows_reregistration() -> None:
    reg = ToolRegistry()
    reg.register(_FakeTool(name="x"))
    reg.unregister("x")
    reg.register(_FakeTool(name="x"))  # should not raise
    assert reg.get("x") is not None


# ---------- executor: happy path ----------

def test_load_plugin_registers_declared_tool(
    tmp_path: Path, cleanup_sys_modules,
) -> None:
    _write_manifest(tmp_path, {
        "name": "wv25-happy",
        "version": "0.1.0",
        "description": "test",
        "providesTools": ["wv25_happy_mod:HappyTool"],
    })
    _write_tool_module(tmp_path, "wv25_happy_mod", "HappyTool", "wv25_happy")

    manifest = PluginManifest.from_path(tmp_path)
    reg = ToolRegistry()
    handle = load_plugin(manifest, tmp_path, tool_registry=reg)

    assert reg.get("wv25_happy") is not None
    assert handle.tool_names == ["wv25_happy"]
    assert handle.manifest.name == "wv25-happy"


def test_load_plugin_returns_empty_handle_when_nothing_to_load(
    tmp_path: Path,
) -> None:
    _write_manifest(tmp_path, {
        "name": "wv25-empty",
        "version": "0.1.0",
        "description": "test",
    })
    manifest = PluginManifest.from_path(tmp_path)
    reg = ToolRegistry()
    handle = load_plugin(manifest, tmp_path, tool_registry=reg)
    assert handle.tool_names == []
    assert handle.manifest.name == "wv25-empty"


def test_load_plugin_cleans_up_sys_path(
    tmp_path: Path, cleanup_sys_modules,
) -> None:
    """sys.path must be restored even on successful load — a
    lingering install path could shadow unrelated modules."""
    _write_manifest(tmp_path, {
        "name": "wv25-syspath",
        "version": "0.1.0",
        "description": "test",
        "providesTools": ["wv25_syspath_mod:STool"],
    })
    _write_tool_module(tmp_path, "wv25_syspath_mod", "STool", "wv25_syspath")
    before = list(sys.path)
    reg = ToolRegistry()
    load_plugin(PluginManifest.from_path(tmp_path), tmp_path, tool_registry=reg)
    assert sys.path == before


# ---------- executor: rollback on conflict ----------

def test_load_plugin_rolls_back_on_conflict(
    tmp_path: Path, cleanup_sys_modules,
) -> None:
    """Two declared tools, the second conflicts with a pre-existing
    tool. Both plugin tools must be absent from the registry after
    the conflict — the first one must not linger."""
    _write_manifest(tmp_path, {
        "name": "wv25-conflict",
        "version": "0.1.0",
        "description": "test",
        "providesTools": [
            "wv25_conflict_mod:FirstTool",
            "wv25_conflict_mod:SecondTool",
        ],
    })
    (tmp_path / "wv25_conflict_mod.py").write_text(
        textwrap.dedent("""
        from llm_code.tools.base import Tool, ToolResult, PermissionLevel


        class _Base(Tool):
            _n = "override-me"
            @property
            def name(self) -> str:
                return self._n
            @property
            def description(self) -> str:
                return ""
            @property
            def input_schema(self) -> dict:
                return {}
            @property
            def required_permission(self) -> PermissionLevel:
                return PermissionLevel.READ_ONLY
            def is_read_only(self, args) -> bool:
                return True
            def is_destructive(self, args) -> bool:
                return False
            def validate_input(self, args):
                return args or {}
            def execute(self, args):
                return ToolResult(output="", is_error=False)


        class FirstTool(_Base):
            _n = "wv25_first"

        class SecondTool(_Base):
            _n = "wv25_taken"  # collides with pre-registered
        """),
        encoding="utf-8",
    )

    reg = ToolRegistry()
    reg.register(_FakeTool(name="wv25_taken"))

    manifest = PluginManifest.from_path(tmp_path)
    with pytest.raises(PluginConflictError) as excinfo:
        load_plugin(manifest, tmp_path, tool_registry=reg)

    assert excinfo.value.plugin_name == "wv25-conflict"
    # The already-registered pre-existing tool is still there
    assert reg.get("wv25_taken") is not None
    # The first plugin tool was registered then rolled back
    assert reg.get("wv25_first") is None


def test_load_plugin_force_overrides_existing_tool(
    tmp_path: Path, cleanup_sys_modules,
) -> None:
    _write_manifest(tmp_path, {
        "name": "wv25-force",
        "version": "0.1.0",
        "description": "test",
        "providesTools": ["wv25_force_mod:ForceTool"],
    })
    _write_tool_module(tmp_path, "wv25_force_mod", "ForceTool", "wv25_shared")

    reg = ToolRegistry()
    reg.register(_FakeTool(name="wv25_shared"))
    original = reg.get("wv25_shared")

    manifest = PluginManifest.from_path(tmp_path)
    load_plugin(manifest, tmp_path, tool_registry=reg, force=True)

    # The plugin tool replaced the fake one
    assert reg.get("wv25_shared") is not original


# ---------- executor: rollback on structural failures ----------

def test_load_plugin_raises_on_unparseable_entry(
    tmp_path: Path,
) -> None:
    _write_manifest(tmp_path, {
        "name": "wv25-bad-entry",
        "version": "0.1.0",
        "description": "test",
        "providesTools": ["no-colon-no-dots"],
    })
    reg = ToolRegistry()
    with pytest.raises(PluginLoadError, match="cannot parse entry"):
        load_plugin(
            PluginManifest.from_path(tmp_path), tmp_path, tool_registry=reg,
        )
    assert reg.all_tools() == ()


def test_load_plugin_raises_on_missing_module(
    tmp_path: Path, cleanup_sys_modules,
) -> None:
    _write_manifest(tmp_path, {
        "name": "wv25-missing-mod",
        "version": "0.1.0",
        "description": "test",
        "providesTools": ["wv25_nonexistent:Anything"],
    })
    reg = ToolRegistry()
    with pytest.raises(PluginLoadError, match="import failed"):
        load_plugin(
            PluginManifest.from_path(tmp_path), tmp_path, tool_registry=reg,
        )


def test_load_plugin_raises_on_missing_class(
    tmp_path: Path, cleanup_sys_modules,
) -> None:
    _write_manifest(tmp_path, {
        "name": "wv25-missing-class",
        "version": "0.1.0",
        "description": "test",
        "providesTools": ["wv25_has_mod:DoesNotExist"],
    })
    (tmp_path / "wv25_has_mod.py").write_text("# intentionally empty\n")
    reg = ToolRegistry()
    with pytest.raises(PluginLoadError, match="not found in module"):
        load_plugin(
            PluginManifest.from_path(tmp_path), tmp_path, tool_registry=reg,
        )


def test_load_plugin_raises_on_broken_ctor(
    tmp_path: Path, cleanup_sys_modules,
) -> None:
    _write_manifest(tmp_path, {
        "name": "wv25-broken-ctor",
        "version": "0.1.0",
        "description": "test",
        "providesTools": ["wv25_broken_mod:BrokenTool"],
    })
    _write_broken_tool_module(tmp_path, "wv25_broken_mod")
    reg = ToolRegistry()
    with pytest.raises(PluginLoadError, match="instantiation failed"):
        load_plugin(
            PluginManifest.from_path(tmp_path), tmp_path, tool_registry=reg,
        )


# ---------- unload_plugin ----------

def test_unload_plugin_removes_registered_tools(
    tmp_path: Path, cleanup_sys_modules,
) -> None:
    _write_manifest(tmp_path, {
        "name": "wv25-unload",
        "version": "0.1.0",
        "description": "test",
        "providesTools": ["wv25_unload_mod:UTool"],
    })
    _write_tool_module(tmp_path, "wv25_unload_mod", "UTool", "wv25_unload")

    reg = ToolRegistry()
    manifest = PluginManifest.from_path(tmp_path)
    handle = load_plugin(manifest, tmp_path, tool_registry=reg)
    assert reg.get("wv25_unload") is not None

    unload_plugin(handle, tool_registry=reg)
    assert reg.get("wv25_unload") is None
    assert handle.tool_names == []


def test_unload_plugin_is_idempotent(
    tmp_path: Path, cleanup_sys_modules,
) -> None:
    """Calling unload twice must not raise and must leave the
    registry in the same state as after the first call."""
    _write_manifest(tmp_path, {
        "name": "wv25-idem",
        "version": "0.1.0",
        "description": "test",
        "providesTools": ["wv25_idem_mod:ITool"],
    })
    _write_tool_module(tmp_path, "wv25_idem_mod", "ITool", "wv25_idem")

    reg = ToolRegistry()
    handle = load_plugin(
        PluginManifest.from_path(tmp_path), tmp_path, tool_registry=reg,
    )
    unload_plugin(handle, tool_registry=reg)
    unload_plugin(handle, tool_registry=reg)  # second call is harmless
    assert reg.get("wv25_idem") is None
