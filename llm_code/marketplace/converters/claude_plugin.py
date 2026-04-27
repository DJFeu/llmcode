"""Claude Code plugin → llmcode manifest converter (v16 M5).

Reads a Claude Code plugin directory shaped like::

    plugin-root/
      .claude-plugin/
        plugin.json
      hooks/
        ...
      mcp/
        ...

and emits an llmcode ``manifest.toml`` text + a list of warnings naming
features that landed outside the 80% coverage band.

Coverage map (the 80% target):

* ``commands`` (string or list)         → ``[[commands]]``
* ``mcpServers`` (object or string)     → ``[[mcp]]``
* ``hooks.<event>`` for events in
  :data:`marketplace.manifest.SUPPORTED_HOOK_EVENTS`
                                         → ``[[hooks]]``
* ``providesTools`` / ``provides_tools`` → ``[providesTools]``
* ``themes`` (Claude-shaped colour map)  → ``[themes.<name>]``
* ``permissions``                        → ``[permissions]``
* ``install.subdir``                     → ``[install].subdir``

Out-of-coverage features (the 20% emitting warnings):

* ``hooks.on_tab_complete`` / ``hooks.on_keystroke`` and any other
  event missing from :data:`SUPPORTED_HOOK_EVENTS` — emit warning,
  skip.
* ``outputStyles``, ``lspServers`` (rare; Claude-specific) — emit
  warning, drop.
* ``agents`` and ``skills`` directories — Claude treats them as part
  of the plugin; llmcode loads them via the existing skills + agent
  loaders directly off disk (no manifest entry needed). Emit an info
  message so plugin authors know the install will Just Work.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from llm_code.logging import get_logger
from llm_code.marketplace.manifest import (
    SUPPORTED_HOOK_EVENTS,
    parse_manifest_text,
    PluginManifest,
)

logger = get_logger(__name__)


# Claude → llmcode hook event aliases. Claude Code plugins use these
# names verbatim in ``hooks.<event>``; llmcode uses identical names so
# the map is mostly a passthrough, but keeping the dict explicit means
# adding ``BeforeBashRun`` (a Claude-only event) won't silently match
# anything we don't support.
_HOOK_EVENT_ALIASES: dict[str, str] = {name: name for name in SUPPORTED_HOOK_EVENTS}


# Claude-specific top-level keys we choose to drop quietly. Anything
# NOT in this set, NOT in our converter coverage, AND NOT a structural
# key (``name``, ``version``) lands in the warnings list so plugin
# authors see what was ignored.
_KNOWN_CLAUDE_KEYS: frozenset[str] = frozenset({
    "name",
    "version",
    "author",
    "description",
    "homepage",
    "repository",
    "keywords",
    "license",
    "commands",
    "agents",
    "skills",
    "hooks",
    "mcpServers",
    "lspServers",
    "outputStyles",
    "providesTools",
    "provides_tools",
    "themes",
    "variables",
    "permissions",
    "install",
    # Claude marketplace shape.
    "marketplace",
})


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def convert(plugin_dir: Path) -> tuple[str, list[str]]:
    """Read a Claude Code plugin directory and emit llmcode manifest TOML.

    Parameters
    ----------
    plugin_dir
        Directory containing ``.claude-plugin/plugin.json``.

    Returns
    -------
    manifest_text
        TOML string ready to write at ``<llmcode-plugin-root>/manifest.toml``
        (or to be passed to :func:`marketplace.manifest.parse_manifest_text`).
    warnings
        Human-readable strings naming features that landed outside the
        coverage band. Empty when the conversion was lossless.

    Raises
    ------
    FileNotFoundError
        ``.claude-plugin/plugin.json`` not present.
    ValueError
        ``plugin.json`` not parseable JSON or missing required ``name``.
    """
    cfg_path = plugin_dir / ".claude-plugin" / "plugin.json"
    if not cfg_path.exists():
        raise FileNotFoundError(
            f"Claude plugin manifest not found at {cfg_path}"
        )

    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Claude plugin.json at {cfg_path} is not valid JSON: {exc}"
        ) from exc

    if not isinstance(cfg, dict):
        raise ValueError(f"Claude plugin.json at {cfg_path} must be a JSON object")
    if not isinstance(cfg.get("name"), str) or not cfg["name"].strip():
        raise ValueError(f"Claude plugin.json at {cfg_path} missing 'name'")

    return _build_manifest(cfg, plugin_dir)


def convert_and_validate(plugin_dir: Path) -> tuple[PluginManifest, list[str]]:
    """Convert + parse + validate in one call.

    Used by the installer (M3) so a Claude plugin that fails llmcode
    validation never lands on disk. The returned :class:`PluginManifest`
    is what the installer feeds to ``executor.load_plugin``.

    Validation errors propagate as :class:`marketplace.validator.ValidationError`.
    """
    from llm_code.marketplace.validator import validate

    text, warnings = convert(plugin_dir)
    manifest = parse_manifest_text(text, source=str(plugin_dir / "manifest.toml"))
    validate(manifest)
    return manifest, warnings


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _build_manifest(cfg: dict[str, Any], plugin_dir: Path) -> tuple[str, list[str]]:
    warnings: list[str] = []

    # Unknown top-level keys → warning. Useful when Anthropic ships a
    # new plugin schema and the converter starts seeing fields it
    # never saw before.
    for key in cfg:
        if key not in _KNOWN_CLAUDE_KEYS:
            warnings.append(
                f"Claude plugin field {key!r} is unknown to llmcode; "
                f"converter dropped it"
            )

    # Out-of-coverage Claude features that we know about but don't
    # support yet. Emit a named warning so plugin authors see the gap.
    if cfg.get("outputStyles"):
        warnings.append(
            "Claude 'outputStyles' is not supported in llmcode; dropped"
        )
    if cfg.get("lspServers"):
        warnings.append(
            "Claude 'lspServers' is not supported in llmcode; dropped"
        )

    lines: list[str] = []

    # ── [plugin] ────────────────────────────────────────────────────
    name = str(cfg["name"]).strip()
    version = str(cfg.get("version", "0.0.1")).strip() or "0.0.1"
    description = str(cfg.get("description", "")).strip()
    author = _author_string(cfg.get("author"))

    lines.append("[plugin]")
    lines.append(f"name = {_toml_str(name)}")
    lines.append(f"version = {_toml_str(version)}")
    if author:
        lines.append(f"author = {_toml_str(author)}")
    if description:
        lines.append(f"description = {_toml_str(description)}")

    # providesTools lives inside [plugin] (per the manifest schema)
    # so TOML parsing is unambiguous regardless of subsequent sections.
    raw_provides = cfg.get("providesTools") or cfg.get("provides_tools") or []
    if isinstance(raw_provides, list) and raw_provides:
        valid_entries: list[str] = []
        for entry in raw_provides:
            if not isinstance(entry, str) or ":" not in entry:
                warnings.append(
                    f"Claude providesTools entry {entry!r} not "
                    f"'module.path:Class'; dropped"
                )
                continue
            valid_entries.append(entry)
        if valid_entries:
            rendered = ", ".join(_toml_str(e) for e in valid_entries)
            lines.append(f"providesTools = [{rendered}]")
    lines.append("")

    # ── [install] ──────────────────────────────────────────────────
    install_section = cfg.get("install") if isinstance(cfg.get("install"), dict) else None
    if install_section:
        subdir = install_section.get("subdir", "")
        if isinstance(subdir, str) and subdir.strip():
            lines.append("[install]")
            lines.append(f"subdir = {_toml_str(subdir)}")
            lines.append("")

    # ── [[hooks]] ──────────────────────────────────────────────────
    raw_hooks = cfg.get("hooks") or {}
    if isinstance(raw_hooks, str):
        # Claude allows hooks-as-file-path. Resolve relative to plugin
        # root and inline the JSON so the llmcode manifest is self-
        # contained. Failure emits a warning and skips.
        hooks_data = _load_hooks_file(plugin_dir, raw_hooks, warnings)
    elif isinstance(raw_hooks, dict):
        hooks_data = raw_hooks
    else:
        hooks_data = {}
        if raw_hooks:
            warnings.append(
                f"Claude 'hooks' is type {type(raw_hooks).__name__}; "
                f"converter expected object or string"
            )

    for event_name, defs in hooks_data.items():
        target = _HOOK_EVENT_ALIASES.get(event_name)
        if target is None:
            warnings.append(
                f"Claude hook event {event_name!r} is outside llmcode "
                f"coverage; dropped"
            )
            continue
        if not isinstance(defs, list):
            # Claude sometimes ships a single object; normalise to list.
            defs = [defs]
        for entry in defs:
            if not isinstance(entry, dict):
                warnings.append(
                    f"Claude hook event {event_name!r} entry is not an "
                    f"object; dropped"
                )
                continue
            command = entry.get("command")
            if not isinstance(command, str) or not command.strip():
                warnings.append(
                    f"Claude hook event {event_name!r} entry missing "
                    f"'command' string; dropped"
                )
                continue
            tool_pattern = entry.get("matcher") or entry.get("tool_pattern")
            lines.append("[[hooks]]")
            lines.append(f"event = {_toml_str(target)}")
            lines.append(f"command = {_toml_str(command)}")
            if isinstance(tool_pattern, str) and tool_pattern.strip():
                lines.append(f"tool_pattern = {_toml_str(tool_pattern)}")
            lines.append("")

    # ── [[mcp]] ────────────────────────────────────────────────────
    raw_mcp = cfg.get("mcpServers")
    if isinstance(raw_mcp, str):
        # File path — load JSON.
        raw_mcp = _load_json_file(plugin_dir, raw_mcp, warnings, "mcpServers")
    if isinstance(raw_mcp, dict):
        for name, mcp_cfg in raw_mcp.items():
            if not isinstance(mcp_cfg, dict):
                warnings.append(
                    f"Claude mcpServers.{name} is not an object; dropped"
                )
                continue
            command = mcp_cfg.get("command")
            if not isinstance(command, str) or not command.strip():
                warnings.append(
                    f"Claude mcpServers.{name} missing 'command'; dropped"
                )
                continue
            args = mcp_cfg.get("args", []) or []
            if not isinstance(args, list):
                warnings.append(
                    f"Claude mcpServers.{name}.args must be array; dropped"
                )
                continue
            lines.append("[[mcp]]")
            lines.append(f"name = {_toml_str(str(name))}")
            lines.append(f"command = {_toml_str(command)}")
            if args:
                rendered_args = ", ".join(_toml_str(str(a)) for a in args)
                lines.append(f"args = [{rendered_args}]")
            lines.append("")

    # ── [[commands]] ───────────────────────────────────────────────
    raw_commands = cfg.get("commands")
    if isinstance(raw_commands, list):
        for entry in raw_commands:
            cmd = _build_command_entry(entry, warnings)
            if cmd is not None:
                lines.extend(cmd)
                lines.append("")
    elif isinstance(raw_commands, str):
        # Claude sometimes ships a glob like ``"commands/*.md"``. We
        # don't materialise files here — those land via the existing
        # custom-commands loader at install time. Emit info warning
        # so plugin authors know the conversion didn't drop them.
        warnings.append(
            f"Claude 'commands' field {raw_commands!r} is a path/glob; "
            f"llmcode loads commands directly from disk — no manifest "
            f"entries emitted"
        )

    # ── [themes.<name>] ────────────────────────────────────────────
    raw_themes = cfg.get("themes") or {}
    if isinstance(raw_themes, dict):
        for theme_name, theme_dict in raw_themes.items():
            if not isinstance(theme_dict, dict):
                warnings.append(
                    f"Claude themes.{theme_name} is not an object; dropped"
                )
                continue
            lines.append(f"[themes.{theme_name}]")
            for key, value in theme_dict.items():
                if not isinstance(value, str):
                    warnings.append(
                        f"Claude themes.{theme_name}.{key} is not a string; dropped"
                    )
                    continue
                lines.append(f"{key} = {_toml_str(value)}")
            lines.append("")

    # ── [variables] ────────────────────────────────────────────────
    raw_variables = cfg.get("variables") or {}
    if isinstance(raw_variables, dict) and raw_variables:
        lines.append("[variables]")
        for key, value in raw_variables.items():
            if not isinstance(key, str) or not isinstance(value, str):
                warnings.append(
                    f"Claude variables.{key!r} non-string entry; dropped"
                )
                continue
            lines.append(f"{key} = {_toml_str(value)}")
        lines.append("")

    # providesTools emitted earlier (must precede any [section]).

    # ── [permissions] ──────────────────────────────────────────────
    raw_perms = cfg.get("permissions") or {}
    if isinstance(raw_perms, dict) and raw_perms:
        lines.append("[permissions]")
        for key, value in raw_perms.items():
            if not isinstance(value, bool):
                warnings.append(
                    f"Claude permissions.{key} must be a boolean; dropped"
                )
                continue
            lines.append(f"{key} = {'true' if value else 'false'}")
        lines.append("")

    # ── Mention agents / skills directories ────────────────────────
    if cfg.get("agents"):
        warnings.append(
            "Claude 'agents' directory will be loaded via llmcode's "
            "existing agent loader (no manifest entry needed)"
        )
    if cfg.get("skills"):
        warnings.append(
            "Claude 'skills' directory will be loaded via llmcode's "
            "existing skill loader (no manifest entry needed)"
        )

    text = "\n".join(lines).rstrip() + "\n"
    return text, warnings


def _build_command_entry(
    entry: Any,
    warnings: list[str],
) -> list[str] | None:
    if not isinstance(entry, dict):
        warnings.append(
            "Claude commands entry is not an object; dropped"
        )
        return None
    name = entry.get("name")
    if not isinstance(name, str) or not name.strip():
        warnings.append(
            "Claude commands entry missing 'name'; dropped"
        )
        return None
    description = entry.get("description", "")
    if description is not None and not isinstance(description, str):
        description = ""
    template = entry.get("prompt_template") or entry.get("prompt") or ""
    if not isinstance(template, str):
        template = ""
    out = ["[[commands]]", f"name = {_toml_str(name)}"]
    if description:
        out.append(f"description = {_toml_str(description)}")
    if template:
        out.append(f"prompt_template = {_toml_str(template)}")
    return out


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _author_string(raw: Any) -> str:
    """Normalise the various ``author`` shapes Claude allows.

    Claude accepts a string, an object with ``name`` + ``email``, or
    nothing. llmcode only stores a single string, so collapse.
    """
    if isinstance(raw, str):
        return raw.strip()
    if isinstance(raw, dict):
        name = str(raw.get("name", "")).strip()
        email = str(raw.get("email", "")).strip()
        if name and email:
            return f"{name} <{email}>"
        if name:
            return name
        if email:
            return email
    return ""


def _load_hooks_file(
    plugin_dir: Path, rel_path: str, warnings: list[str],
) -> dict[str, Any]:
    """Load Claude's hooks.json file and return the inner ``hooks`` dict.

    Claude wraps the events under a top-level ``"hooks"`` key in some
    plugin templates and inline at the root in others. Handle both.
    """
    parsed = _load_json_file(plugin_dir, rel_path, warnings, "hooks")
    if not isinstance(parsed, dict):
        return {}
    if isinstance(parsed.get("hooks"), dict):
        return parsed["hooks"]
    return parsed


def _load_json_file(
    plugin_dir: Path, rel_path: str, warnings: list[str], field_name: str,
) -> Any:
    target = plugin_dir / rel_path
    if not target.exists():
        warnings.append(
            f"Claude {field_name} file path {rel_path!r} not found in plugin"
        )
        return None
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        warnings.append(
            f"Claude {field_name} file {rel_path!r} unreadable: {exc}"
        )
        return None


def _toml_str(value: str) -> str:
    """Emit a TOML basic string with escaped specials.

    We deliberately don't use ``tomli_w`` (extra dep) — basic strings
    cover the entire Claude plugin schema we touch. Multi-line content
    is rare; when it appears, ``\\n`` keeps the manifest single-line.
    """
    escaped = (
        value.replace("\\", "\\\\")
        .replace("\"", "\\\"")
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )
    return f'"{escaped}"'
