"""Plugin installer — local copy, npm, and GitHub install strategies."""
from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from typing import Any

from llm_code.marketplace.plugin import InstalledPlugin, PluginManifest


# State file format:
# {
#   "plugin-name": {"enabled": true, "installed_from": "local"}
# }


class PluginInstaller:
    """Manages installation, removal, and enumeration of plugins."""

    def __init__(self, install_dir: Path) -> None:
        self._install_dir = install_dir
        self._install_dir.mkdir(parents=True, exist_ok=True)
        self._state_path = self._install_dir / "state.json"

    # ------------------------------------------------------------------
    # State helpers
    # ------------------------------------------------------------------

    def _read_state(self) -> dict[str, dict[str, Any]]:
        if not self._state_path.exists():
            return {}
        return json.loads(self._state_path.read_text(encoding="utf-8"))

    def _write_state(self, state: dict[str, dict[str, Any]]) -> None:
        self._state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")

    # ------------------------------------------------------------------
    # Install strategies
    # ------------------------------------------------------------------

    def install_from_local(self, source: Path) -> Path:
        """Copy a local plugin directory into the install directory.

        Returns the destination path.
        """
        manifest = PluginManifest.from_path(source)
        dest = self._install_dir / manifest.name
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(source, dest)

        state = self._read_state()
        state[manifest.name] = {"enabled": True, "installed_from": "local"}
        self._write_state(state)

        return dest

    async def install_from_npm(self, package: str, version: str = "latest") -> Path:
        """Install a plugin package via npm --prefix (uses execvp, not shell).

        Returns the destination path.
        """
        dest = self._install_dir / package.replace("/", "__")
        dest.mkdir(parents=True, exist_ok=True)
        pkg_spec = f"{package}@{version}" if version != "latest" else package

        proc = await asyncio.create_subprocess_exec(
            "npm", "install", "--prefix", str(dest), pkg_spec,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()

        state = self._read_state()
        state[package] = {"enabled": True, "installed_from": "npm"}
        self._write_state(state)

        return dest

    async def install_from_github(self, repo: str, ref: str = "main") -> Path:
        """Clone a GitHub repository as a plugin using git clone (uses execvp, not shell).

        Returns the destination path.
        """
        name = repo.replace("/", "__")
        dest = self._install_dir / name
        if dest.exists():
            shutil.rmtree(dest)

        url = f"https://github.com/{repo}.git"
        proc = await asyncio.create_subprocess_exec(
            "git", "clone", "--depth", "1", "--branch", ref, url, str(dest),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()

        state = self._read_state()
        state[name] = {"enabled": True, "installed_from": "github"}
        self._write_state(state)

        return dest

    # ------------------------------------------------------------------
    # Management
    # ------------------------------------------------------------------

    def uninstall(self, name: str) -> None:
        """Remove an installed plugin by name."""
        dest = self._install_dir / name
        if dest.exists():
            shutil.rmtree(dest)

        state = self._read_state()
        state.pop(name, None)
        self._write_state(state)

    def list_installed(self) -> list[InstalledPlugin]:
        """Return all installed plugins, merging directory scan with state.json.

        Plugins with .claude-plugin/plugin.json use its metadata.
        Plugins without a manifest (e.g. from marketplace subdir install)
        are still detected if they have a directory and state.json entry.
        """
        state = self._read_state()
        plugins: list[InstalledPlugin] = []
        seen_names: set[str] = set()

        for entry in sorted(self._install_dir.iterdir()):
            if not entry.is_dir():
                continue
            name = entry.name

            try:
                manifest = PluginManifest.from_path(entry)
            except FileNotFoundError:
                # No .claude-plugin/plugin.json — create minimal manifest from dir name
                manifest = PluginManifest(name=name, version="0.0.0", description="")

            entry_state = state.get(name, {})
            enabled = bool(entry_state.get("enabled", True))
            installed_from = str(entry_state.get("installed_from", "local"))

            seen_names.add(name)
            plugins.append(
                InstalledPlugin(
                    manifest=manifest,
                    path=entry,
                    enabled=enabled,
                    installed_from=installed_from,
                )
            )

        return plugins

    def enable(self, name: str) -> None:
        """Mark a plugin as enabled in state.json."""
        state = self._read_state()
        entry = state.setdefault(name, {})
        state[name] = {**entry, "enabled": True}
        self._write_state(state)

    def disable(self, name: str) -> None:
        """Mark a plugin as disabled in state.json."""
        state = self._read_state()
        entry = state.setdefault(name, {})
        state[name] = {**entry, "enabled": False}
        self._write_state(state)
