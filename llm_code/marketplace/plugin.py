"""Plugin manifest and installed-plugin models for the marketplace subsystem."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
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
    skills: tuple[str, ...] | None = None
    hooks: dict[str, Any] | None = None
    mcp_servers: tuple[dict[str, Any], ...] | None = None
    lsp_servers: tuple[dict[str, Any], ...] | None = None

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

        return cls(
            name=data["name"],
            version=data["version"],
            description=data["description"],
            author=data.get("author"),
            homepage=data.get("homepage"),
            repository=data.get("repository"),
            keywords=tuple(keywords_raw) if keywords_raw else (),
            commands=tuple(commands_raw) if commands_raw else (),
            agents=tuple(agents_raw) if agents_raw is not None else None,
            skills=tuple(skills_raw) if skills_raw is not None else None,
            hooks=hooks_raw,
            mcp_servers=tuple(mcp_raw) if mcp_raw is not None else None,
            lsp_servers=tuple(lsp_raw) if lsp_raw is not None else None,
        )


@dataclass(frozen=True)
class InstalledPlugin:
    """Immutable snapshot of an installed plugin with runtime state."""

    manifest: PluginManifest
    path: Path
    enabled: bool
    scope: str = "user"
    installed_from: str = "local"
