"""Plugin installer — local copy, npm, and GitHub install strategies."""
from __future__ import annotations

import asyncio
import datetime
import json
import logging
import shutil
from pathlib import Path
from typing import Any

from llm_code.marketplace.plugin import InstalledPlugin, PluginManifest

logger = logging.getLogger(__name__)


class SecurityScanError(Exception):
    """Raised when a plugin fails the security scan."""

    def __init__(self, findings: list[str]) -> None:
        self.findings = findings
        super().__init__(f"Security scan found {len(findings)} issue(s): {'; '.join(findings)}")


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
    # Security scanning
    # ------------------------------------------------------------------

    _TEXT_SUFFIXES = frozenset({
        ".py", ".js", ".ts", ".sh", ".bash", ".json", ".yaml", ".yml",
        ".toml", ".md", ".txt", ".cfg", ".ini", ".env", ".conf",
    })

    def scan_plugin(self, plugin_dir: Path) -> list[str]:
        """Scan a plugin directory for security issues.

        Returns a list of findings (empty if clean).
        Checks: embedded secrets, suspicious scripts, oversized files.
        """
        from llm_code.runtime.secret_scanner import scan_output

        findings: list[str] = []
        max_file_size = 1_000_000  # 1 MB

        for path in plugin_dir.rglob("*"):
            if not path.is_file():
                continue

            # Check file size
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if size > max_file_size:
                findings.append(f"Oversized file ({size} bytes): {path.relative_to(plugin_dir)}")
                continue

            # Only scan text files for secrets
            if path.suffix.lower() not in self._TEXT_SUFFIXES:
                continue

            try:
                content = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue

            _, secrets = scan_output(content)
            for s in secrets:
                findings.append(f"{path.relative_to(plugin_dir)}: {s}")

        # Check for suspicious postinstall scripts in package.json
        pkg_json = plugin_dir / "package.json"
        if pkg_json.exists():
            try:
                pkg = json.loads(pkg_json.read_text(encoding="utf-8"))
                scripts = pkg.get("scripts", {})
                for key in ("postinstall", "preinstall", "install"):
                    if key in scripts:
                        findings.append(
                            f"package.json has '{key}' script: {scripts[key][:100]}"
                        )
            except (json.JSONDecodeError, OSError):
                pass

        self._write_audit_log(plugin_dir, findings)
        return findings

    @staticmethod
    def _write_audit_log(plugin_dir: Path, findings: list[str]) -> None:
        """Append scan result to ~/.llmcode/security-audit.jsonl."""
        audit_path = Path.home() / ".llmcode" / "security-audit.jsonl"
        entry = {
            "timestamp": datetime.datetime.now(tz=datetime.timezone.utc).isoformat(),
            "plugin": plugin_dir.name,
            "path": str(plugin_dir),
            "findings": findings,
            "passed": len(findings) == 0,
        }
        try:
            audit_path.parent.mkdir(parents=True, exist_ok=True)
            with audit_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except OSError:
            logger.debug("Failed to write security audit log to %s", audit_path)

    # ------------------------------------------------------------------
    # Install strategies
    # ------------------------------------------------------------------

    def install_from_local(self, source: Path) -> Path:
        """Copy a local plugin directory into the install directory.

        Returns the destination path.
        Raises SecurityScanError if secrets or suspicious files are found.
        """
        # Pre-install scan on source directory
        findings = self.scan_plugin(source)
        if findings:
            raise SecurityScanError(findings)

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
        """Install a plugin via npm --prefix (uses execvp, no shell).

        Returns the destination path.
        Logs warnings if security scan finds issues (non-blocking for npm).
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

        # Post-install scan (log warnings, don't block)
        findings = self.scan_plugin(dest)
        for f in findings:
            logger.warning("Plugin %s security scan: %s", package, f)

        state = self._read_state()
        state[package] = {"enabled": True, "installed_from": "npm"}
        self._write_state(state)

        return dest

    async def install_from_github(self, repo: str, ref: str = "main") -> Path:
        """Clone a GitHub repo as a plugin via git clone (execvp, no shell).

        Returns the destination path.
        Raises SecurityScanError if secrets or suspicious files are found.
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

        # Post-clone scan (block on findings — untrusted source)
        findings = self.scan_plugin(dest)
        if findings:
            shutil.rmtree(dest, ignore_errors=True)
            raise SecurityScanError(findings)

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
