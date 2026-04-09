"""Plugin manifest and installed-plugin models for the marketplace subsystem."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class PluginManifest:
    """Immutable representation of a parsed .claude-plugin/plugin.json manifest."""

    name: str
    version: str
    description: str
    author: dict[str, Any] | None = None
    homepage: str | None = None
    repository: str | None = None
    keywords: tuple[str, ...] = ()
    commands: tuple[str, ...] = ()
    agents: tuple[str, ...] | None = None
    skills: str | tuple[str, ...] | None = None
    hooks: dict[str, Any] | None = None
    mcp_servers: tuple[dict[str, Any], ...] | None = None
    lsp_servers: tuple[dict[str, Any], ...] | None = None
    # Wave2-5: Python tools exported by the plugin, in the form
    # ``"package.module:ClassName"``. The executor resolves each
    # entry by importing the module (with the plugin install path
    # temporarily on sys.path) and calling the class with no args,
    # then passes the resulting Tool to ToolRegistry.register.
    provides_tools: tuple[str, ...] = ()
    # Declared capability envelope the plugin needs. The executor
    # blocks dangerous capabilities (subprocess, fs_write, env)
    # unless --force is passed. Network is allowed by default.
    # Expected keys (all optional, all default False):
    # "network", "fs_write", "subprocess", "env".
    permissions: dict[str, Any] | None = None

    @classmethod
    def from_path(cls, plugin_dir: Path) -> "PluginManifest":
        """Read .claude-plugin/plugin.json from plugin_dir and return a PluginManifest.

        Raises FileNotFoundError if plugin_dir does not exist or plugin.json is missing.
        """
        if not plugin_dir.exists():
            raise FileNotFoundError(f"Plugin directory not found: {plugin_dir}")

        manifest_path = plugin_dir / ".claude-plugin" / "plugin.json"
        if not manifest_path.exists():
            raise FileNotFoundError(f"Plugin manifest not found: {manifest_path}")

        data: dict[str, Any] = json.loads(manifest_path.read_text(encoding="utf-8"))

        # Map camelCase JSON keys to snake_case fields
        mcp_raw = data.get("mcpServers")
        lsp_raw = data.get("lspServers")

        keywords_raw = data.get("keywords", [])
        commands_raw = data.get("commands", [])
        agents_raw = data.get("agents")
        skills_raw = data.get("skills")
        hooks_raw = data.get("hooks")
        # Wave2-5: both camelCase and snake_case accepted so plugin
        # authors can pick whichever matches the rest of their manifest.
        tools_raw = data.get("providesTools") or data.get("provides_tools") or []
        perms_raw = data.get("permissions")

        return cls(
            name=data["name"],
            version=str(data.get("version", "0.0.0")),
            description=str(data.get("description", "")),
            author=data.get("author"),
            homepage=data.get("homepage"),
            repository=data.get("repository"),
            keywords=tuple(keywords_raw) if keywords_raw else (),
            commands=tuple(commands_raw) if commands_raw else (),
            agents=tuple(agents_raw) if agents_raw is not None else None,
            skills=skills_raw if isinstance(skills_raw, str) else (tuple(skills_raw) if skills_raw is not None else None),
            hooks=hooks_raw,
            mcp_servers=tuple(mcp_raw) if mcp_raw is not None else None,
            lsp_servers=tuple(lsp_raw) if lsp_raw is not None else None,
            provides_tools=tuple(tools_raw) if tools_raw else (),
            permissions=perms_raw if isinstance(perms_raw, dict) else None,
        )


@dataclass(frozen=True)
class InstalledPlugin:
    """Immutable snapshot of an installed plugin with runtime state."""

    manifest: PluginManifest
    path: Path
    enabled: bool
    scope: str = "user"
    installed_from: str = "local"
