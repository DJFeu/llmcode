"""Manifest validator (v16 M5).

Catches semantic errors the parser can't (semver, hook event whitelist,
tool name regex, MCP command path, etc.). The parser raises on bad
TOML *shape*; the validator raises on bad *contents* of an
otherwise-well-shaped manifest.

Design:
* Pure function — accepts a :class:`PluginManifest`, returns nothing,
  raises :class:`ValidationError` on first failure.
* Error messages always include the section path
  (``[hooks][2].event``) so plugin authors see exactly where to look.
* The installer (M3) calls this BEFORE any disk write so a malformed
  manifest never lands a half-installed plugin in
  ``~/.llmcode/plugins``.
"""
from __future__ import annotations

import re
from typing import Final

from llm_code.marketplace.manifest import (
    SUPPORTED_HOOK_EVENTS,
    PluginManifest,
)

# Semver-ish: ``MAJOR.MINOR.PATCH`` with optional ``-prerelease`` and
# ``+build`` tail. Mirrors the practical subset npm + cargo use; we
# don't need full SemVer 2.0.0 parsing for an install-time gate.
_SEMVER_RE: Final[re.Pattern[str]] = re.compile(
    r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)

# Tool / command names — alphanumeric + underscore + hyphen, must
# start with a letter. Loose enough to cover the conventions used
# across llmcode (snake_case) and Claude Code plugins (kebab-case).
_NAME_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z][A-Za-z0-9_\-]{0,63}$")

# providesTools entry shape — ``module.path:ClassName``.
_PROVIDES_TOOL_RE: Final[re.Pattern[str]] = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_\.]*:[A-Za-z_][A-Za-z0-9_]*$"
)

# Permission keys the executor knows how to gate. New keys here must
# also land in ``executor._DANGEROUS_CAPS`` if they're security
# sensitive — the validator just rejects unknown keys outright.
_PERMISSION_KEYS: Final[frozenset[str]] = frozenset({
    "network",
    "fs_write",
    "subprocess",
    "env",
})


class ValidationError(ValueError):
    """Raised when a manifest has a structurally-valid but semantically
    invalid field.

    Plugin authors see the section path + offending value; the
    installer surfaces this as ``"plugin install blocked: <message>"``.
    """


def validate(manifest: PluginManifest) -> None:
    """Run every semantic check; raise on first failure.

    The order tries to surface the most fixable error first (semver
    typo before hook event typo before name regex), but every plugin
    that passes the parser will eventually hit each check, so the
    order is more about UX than correctness.
    """
    _check_plugin_section(manifest)
    _check_hooks(manifest)
    _check_mcp(manifest)
    _check_commands(manifest)
    _check_themes(manifest)
    _check_provides_tools(manifest)
    _check_permissions(manifest)


def _check_plugin_section(manifest: PluginManifest) -> None:
    if not _NAME_RE.match(manifest.name):
        raise ValidationError(
            f"[plugin].name {manifest.name!r}: must match {_NAME_RE.pattern}"
        )
    if not _SEMVER_RE.match(manifest.version):
        raise ValidationError(
            f"[plugin].version {manifest.version!r}: not a valid "
            f"semver (e.g. 1.0.0 or 2.1.0-beta.3)"
        )


def _check_hooks(manifest: PluginManifest) -> None:
    for idx, hook in enumerate(manifest.hooks):
        if hook.event not in SUPPORTED_HOOK_EVENTS:
            raise ValidationError(
                f"[hooks][{idx}].event {hook.event!r} is not a supported "
                f"event. Supported: {', '.join(SUPPORTED_HOOK_EVENTS)}"
            )
        # Reject obviously dangerous shell metacharacters in the
        # command path. The hook bus runs this through subprocess
        # without a shell, but plugin authors who pipe through ``sh``
        # via ``command = "bash -c '...'"`` are likely confusing
        # themselves — a cleaner pattern is a single executable.
        if "`" in hook.command or "$(" in hook.command:
            raise ValidationError(
                f"[hooks][{idx}].command contains shell-substitution "
                f"syntax (`` or $()) which is not interpreted; use a "
                f"single executable + args instead"
            )


def _check_mcp(manifest: PluginManifest) -> None:
    seen: set[str] = set()
    for idx, mcp in enumerate(manifest.mcp):
        if not _NAME_RE.match(mcp.name):
            raise ValidationError(
                f"[mcp][{idx}].name {mcp.name!r}: must match {_NAME_RE.pattern}"
            )
        if mcp.name in seen:
            raise ValidationError(
                f"[mcp][{idx}].name {mcp.name!r}: duplicate within manifest"
            )
        seen.add(mcp.name)
        if not mcp.command.strip():
            raise ValidationError(
                f"[mcp][{idx}].command must be a non-empty string"
            )


def _check_commands(manifest: PluginManifest) -> None:
    seen: set[str] = set()
    for idx, cmd in enumerate(manifest.commands):
        if not _NAME_RE.match(cmd.name):
            raise ValidationError(
                f"[commands][{idx}].name {cmd.name!r}: must match {_NAME_RE.pattern}"
            )
        if cmd.name in seen:
            raise ValidationError(
                f"[commands][{idx}].name {cmd.name!r}: duplicate within manifest"
            )
        seen.add(cmd.name)


def _check_themes(manifest: PluginManifest) -> None:
    for theme_name, _pairs in manifest.themes:
        if not _NAME_RE.match(theme_name):
            raise ValidationError(
                f"[themes].{theme_name!r}: name must match {_NAME_RE.pattern}"
            )


def _check_provides_tools(manifest: PluginManifest) -> None:
    seen: set[str] = set()
    for idx, entry in enumerate(manifest.provides_tools):
        if not _PROVIDES_TOOL_RE.match(entry):
            raise ValidationError(
                f"[providesTools][{idx}] {entry!r}: must be 'module.path:ClassName'"
            )
        if entry in seen:
            raise ValidationError(
                f"[providesTools][{idx}] {entry!r}: duplicate entry"
            )
        seen.add(entry)


def _check_permissions(manifest: PluginManifest) -> None:
    for key, _value in manifest.permissions:
        if key not in _PERMISSION_KEYS:
            raise ValidationError(
                f"[permissions].{key!r}: unknown permission key. "
                f"Known: {', '.join(sorted(_PERMISSION_KEYS))}"
            )
