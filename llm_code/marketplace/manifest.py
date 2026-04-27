"""Formal llmcode plugin manifest schema (v16 M5).

Replaces the ad-hoc dict shape that ``installer.py`` consumed in wave 1.
The on-disk file is ``manifest.toml`` at the install root and parses
into a frozen :class:`PluginManifest` covering:

* ``[plugin]`` — name, version, author, description (required: name +
  version, validated as semver-ish ``MAJOR.MINOR.PATCH``).
* ``[install]`` — optional ``subdir`` for monorepo plugin packages.
* ``[[hooks]]`` — array of (event, command, optional ``tool_pattern``)
  triples. Event names are restricted to the runtime hook bus surface;
  unknown events are rejected by :mod:`marketplace.validator`.
* ``[[mcp]]`` — array of (name, command, optional args) inline MCP
  server definitions. Subagent factory consumes these in M7.
* ``[[commands]]`` — slash command definitions with prompt templates.
* ``[themes.<name>]`` — Rich-style theme dicts.
* ``[variables]`` — string templates substituted at hook/command
  execution time. Not interpolated here; the runtime hook bus does it.
* ``[providesTools]`` — ``"package.module:Class"`` strings consumed by
  :mod:`marketplace.executor` to register Python tool callables.

Design constraints:
* Frozen dataclasses everywhere (immutability rule).
* Strict TOML — unknown top-level keys raise :class:`ManifestError`.
  Known sections are exhaustively listed in :data:`KNOWN_SECTIONS`.
* No I/O happens in ``__post_init__`` — :func:`load_manifest` is the
  single entry point and the only place that touches the filesystem.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from llm_code.logging import get_logger

logger = get_logger(__name__)

try:
    import tomllib  # py3.11+
except ImportError:  # pragma: no cover - py3.10 fallback
    import tomli as tomllib  # type: ignore[no-redef]


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ManifestError(ValueError):
    """Raised when ``manifest.toml`` cannot be parsed.

    The message names the offending section/key path so plugin authors
    see exactly where to look. Validator errors (semver, hook event
    whitelist, etc.) are raised as :class:`ValidationError` from the
    sibling ``validator`` module.
    """


# ---------------------------------------------------------------------------
# Section dataclasses
# ---------------------------------------------------------------------------


# Hook events the runtime hook bus understands. The Claude converter
# (``converters/claude_plugin``) maps Claude-only events into this
# whitelist or emits a warning for anything outside coverage. Keep in
# sync with ``runtime/hook_lifecycle.py`` and ``runtime/hook_dispatcher.py``.
SUPPORTED_HOOK_EVENTS: tuple[str, ...] = (
    "on_pre_tool_use",
    "on_post_tool_use",
    "on_session_start",
    "on_session_stop",
    "on_user_message",
)


@dataclass(frozen=True)
class HookSpec:
    """One hook entry in ``[[hooks]]``.

    ``tool_pattern`` is an fnmatch-style glob that constrains which
    tools fire the hook (``edit_*`` only, etc.). ``None`` means "every
    tool" — same default the runtime hook bus uses for unfiltered hooks.
    """

    event: str
    command: str
    tool_pattern: str | None = None


@dataclass(frozen=True)
class MCPSpec:
    """One inline MCP server entry in ``[[mcp]]``.

    The subagent factory (M7) spawns ``command`` + ``args`` via
    ``subprocess.Popen`` with the MCP stdio protocol. ``args`` is a
    tuple to keep the manifest hashable + immutable.
    """

    name: str
    command: str
    args: tuple[str, ...] = ()


@dataclass(frozen=True)
class CommandSpec:
    """One slash command entry in ``[[commands]]``.

    ``prompt_template`` is a string with ``{{var}}`` placeholders that
    the runtime substitutes from ``[variables]`` and the active session
    context (``git_diff``, ``cwd``, etc.). Untyped here so plugin
    authors can use any flat string shape.
    """

    name: str
    description: str = ""
    prompt_template: str = ""


@dataclass(frozen=True)
class PluginManifest:
    """Frozen manifest emitted by :func:`load_manifest`.

    All collection fields are tuples / frozen mappings so the dataclass
    stays hashable and safe to share across coroutines.
    """

    # [plugin]
    name: str
    version: str
    author: str = ""
    description: str = ""

    # [install]
    subdir: str = ""

    # arrays
    hooks: tuple[HookSpec, ...] = ()
    mcp: tuple[MCPSpec, ...] = ()
    commands: tuple[CommandSpec, ...] = ()

    # mappings — frozen via tuple-of-pairs; consumers convert as needed.
    themes: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = ()
    variables: tuple[tuple[str, str], ...] = ()

    # providesTools — same shape the wave 1 executor consumed.
    provides_tools: tuple[str, ...] = ()

    # Permissions envelope (mirrors wave 1's PluginManifest dict shape
    # so the executor's permission gate keeps working unchanged).
    permissions: tuple[tuple[str, bool], ...] = ()

    # Round-trip warnings emitted at parse time (e.g. "unknown section
    # X — ignored"). Surfaced by the installer so plugin authors see
    # what was dropped without having to rerun in verbose mode.
    warnings: tuple[str, ...] = field(default_factory=tuple)

    # ------------------------------------------------------------------
    # Convenience views
    # ------------------------------------------------------------------

    def themes_dict(self) -> dict[str, dict[str, str]]:
        """Return ``[themes]`` as a regular nested dict (not frozen)."""
        return {name: dict(pairs) for name, pairs in self.themes}

    def variables_dict(self) -> dict[str, str]:
        """Return ``[variables]`` as a regular dict."""
        return dict(self.variables)

    def permissions_dict(self) -> dict[str, bool]:
        """Return ``[permissions]`` as a regular dict.

        Used by ``executor.load_plugin``'s capability gate.
        """
        return dict(self.permissions)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


# Sections the parser knows about. Anything else is rejected (strict
# parsing — a typo like ``[hookz]`` should fail loudly, not silently
# do nothing).
KNOWN_SECTIONS: frozenset[str] = frozenset({
    "plugin",
    "install",
    "hooks",
    "mcp",
    "commands",
    "themes",
    "variables",
    "providesTools",
    "permissions",
})


def load_manifest(path: Path) -> PluginManifest:
    """Read ``manifest.toml`` from disk and return a :class:`PluginManifest`.

    Parameters
    ----------
    path
        Either the manifest file directly or a directory containing
        ``manifest.toml`` at its root.

    Raises
    ------
    ManifestError
        File missing, unparsable TOML, unknown top-level section, or a
        required field absent.
    """
    if path.is_dir():
        toml_path = path / "manifest.toml"
    else:
        toml_path = path

    if not toml_path.exists():
        raise ManifestError(f"manifest.toml not found at {toml_path}")

    try:
        raw_bytes = toml_path.read_bytes()
        data = tomllib.loads(raw_bytes.decode("utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        raise ManifestError(f"cannot read manifest at {toml_path}: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ManifestError(
            f"manifest.toml at {toml_path} is not valid TOML: {exc}"
        ) from exc

    return _parse_manifest_dict(data, source=str(toml_path))


def parse_manifest_text(text: str, *, source: str = "<string>") -> PluginManifest:
    """Parse a manifest from raw TOML text.

    Used by the Claude converter to round-trip its emitted manifest
    through the same parser the installer runs at install time, so the
    converter never produces something the installer would reject.
    """
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ManifestError(f"manifest text from {source} is not valid TOML: {exc}") from exc
    return _parse_manifest_dict(data, source=source)


def _parse_manifest_dict(data: dict[str, Any], *, source: str) -> PluginManifest:
    warnings: list[str] = []

    unknown = sorted(set(data.keys()) - KNOWN_SECTIONS)
    if unknown:
        # Strict mode — surface as ValidationError-equivalent. We raise
        # ManifestError here because the parser is the layer that
        # observed the bad section; the validator catches semantic
        # errors (semver, etc.) downstream.
        raise ManifestError(
            f"unknown section(s) in {source}: {', '.join(unknown)}. "
            f"Known sections: {', '.join(sorted(KNOWN_SECTIONS))}"
        )

    # ── [plugin] ────────────────────────────────────────────────────
    plugin_section = data.get("plugin")
    if not isinstance(plugin_section, dict):
        raise ManifestError(
            f"missing required section [plugin] in {source}"
        )
    name = plugin_section.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ManifestError(f"[plugin].name must be a non-empty string in {source}")
    version = plugin_section.get("version")
    if not isinstance(version, str) or not version.strip():
        raise ManifestError(f"[plugin].version must be a non-empty string in {source}")
    author = plugin_section.get("author", "")
    if author is not None and not isinstance(author, str):
        raise ManifestError(f"[plugin].author must be a string in {source}")
    description = plugin_section.get("description", "")
    if description is not None and not isinstance(description, str):
        raise ManifestError(f"[plugin].description must be a string in {source}")

    # ``providesTools`` lives inside [plugin] so TOML parsing is
    # unambiguous regardless of section ordering. Both the camelCase
    # and snake_case forms are accepted so plugin authors can match
    # the rest of their TOML conventions.
    plugin_provides = (
        plugin_section.get("providesTools")
        or plugin_section.get("provides_tools")
        or []
    )

    # ── [install] ──────────────────────────────────────────────────
    install_section = data.get("install", {})
    if install_section and not isinstance(install_section, dict):
        raise ManifestError(f"[install] must be a table in {source}")
    subdir = install_section.get("subdir", "") if isinstance(install_section, dict) else ""
    if subdir and not isinstance(subdir, str):
        raise ManifestError(f"[install].subdir must be a string in {source}")

    # ── [[hooks]] ──────────────────────────────────────────────────
    hooks_raw = data.get("hooks", [])
    hooks = tuple(_parse_hooks(hooks_raw, source=source))

    # ── [[mcp]] ────────────────────────────────────────────────────
    mcp_raw = data.get("mcp", [])
    mcp = tuple(_parse_mcp(mcp_raw, source=source))

    # ── [[commands]] ───────────────────────────────────────────────
    commands_raw = data.get("commands", [])
    commands = tuple(_parse_commands(commands_raw, source=source))

    # ── [themes] ───────────────────────────────────────────────────
    themes_raw = data.get("themes", {})
    themes = tuple(_parse_themes(themes_raw, source=source))

    # ── [variables] ────────────────────────────────────────────────
    variables_raw = data.get("variables", {})
    variables = tuple(_parse_variables(variables_raw, source=source))

    # ── providesTools (top-level OR inside [plugin]) ────────────────
    # Top-level lookup is preserved for backward compat with the
    # earliest test fixtures; new manifests should use
    # ``[plugin].providesTools`` to keep TOML parsing unambiguous.
    top_level_provides = data.get("providesTools", [])
    if plugin_provides and top_level_provides:
        raise ManifestError(
            f"providesTools declared both at top level and inside [plugin] "
            f"in {source}; use only [plugin].providesTools"
        )
    provides_raw = plugin_provides or top_level_provides
    if provides_raw and not isinstance(provides_raw, list):
        raise ManifestError(
            f"providesTools must be an array of strings in {source}"
        )
    provides_tools: list[str] = []
    for entry in provides_raw or []:
        if not isinstance(entry, str) or not entry.strip():
            raise ManifestError(
                f"providesTools entries must be non-empty strings in {source}"
            )
        provides_tools.append(entry)

    # ── [permissions] ──────────────────────────────────────────────
    perms_raw = data.get("permissions", {})
    if perms_raw and not isinstance(perms_raw, dict):
        raise ManifestError(f"[permissions] must be a table in {source}")
    perm_pairs: list[tuple[str, bool]] = []
    for key, value in (perms_raw or {}).items():
        if not isinstance(key, str):
            raise ManifestError(f"[permissions] keys must be strings in {source}")
        if not isinstance(value, bool):
            raise ManifestError(
                f"[permissions].{key} must be a boolean in {source}"
            )
        perm_pairs.append((key, value))

    return PluginManifest(
        name=name.strip(),
        version=version.strip(),
        author=(author or "").strip(),
        description=(description or "").strip(),
        subdir=(subdir or "").strip(),
        hooks=hooks,
        mcp=mcp,
        commands=commands,
        themes=themes,
        variables=variables,
        provides_tools=tuple(provides_tools),
        permissions=tuple(perm_pairs),
        warnings=tuple(warnings),
    )


# ---------------------------------------------------------------------------
# Section parsers
# ---------------------------------------------------------------------------


def _parse_hooks(raw: Any, *, source: str) -> list[HookSpec]:
    if not raw:
        return []
    if not isinstance(raw, list):
        raise ManifestError(f"[[hooks]] must be an array of tables in {source}")
    out: list[HookSpec] = []
    for idx, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise ManifestError(
                f"[[hooks]] entry #{idx} in {source} is not a table"
            )
        event = entry.get("event")
        if not isinstance(event, str) or not event.strip():
            raise ManifestError(
                f"[[hooks]] entry #{idx} in {source} missing 'event' string"
            )
        command = entry.get("command")
        if not isinstance(command, str) or not command.strip():
            raise ManifestError(
                f"[[hooks]] entry #{idx} ({event}) in {source} "
                f"missing 'command' string"
            )
        tool_pattern = entry.get("tool_pattern")
        if tool_pattern is not None and not isinstance(tool_pattern, str):
            raise ManifestError(
                f"[[hooks]] entry #{idx} in {source} 'tool_pattern' "
                f"must be a string"
            )
        out.append(
            HookSpec(
                event=event.strip(),
                command=command.strip(),
                tool_pattern=tool_pattern.strip() if tool_pattern else None,
            )
        )
    return out


def _parse_mcp(raw: Any, *, source: str) -> list[MCPSpec]:
    if not raw:
        return []
    if not isinstance(raw, list):
        raise ManifestError(f"[[mcp]] must be an array of tables in {source}")
    out: list[MCPSpec] = []
    for idx, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise ManifestError(
                f"[[mcp]] entry #{idx} in {source} is not a table"
            )
        name = entry.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ManifestError(
                f"[[mcp]] entry #{idx} in {source} missing 'name' string"
            )
        command = entry.get("command")
        if not isinstance(command, str) or not command.strip():
            raise ManifestError(
                f"[[mcp]] entry #{idx} ({name}) in {source} "
                f"missing 'command' string"
            )
        args_raw = entry.get("args", [])
        if args_raw and not isinstance(args_raw, list):
            raise ManifestError(
                f"[[mcp]] entry #{idx} ({name}) 'args' must be an array"
            )
        args: list[str] = []
        for arg in args_raw or []:
            if not isinstance(arg, str):
                raise ManifestError(
                    f"[[mcp]] entry #{idx} ({name}) 'args' must be strings"
                )
            args.append(arg)
        out.append(MCPSpec(name=name.strip(), command=command.strip(), args=tuple(args)))
    return out


def _parse_commands(raw: Any, *, source: str) -> list[CommandSpec]:
    if not raw:
        return []
    if not isinstance(raw, list):
        raise ManifestError(
            f"[[commands]] must be an array of tables in {source}"
        )
    out: list[CommandSpec] = []
    for idx, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise ManifestError(
                f"[[commands]] entry #{idx} in {source} is not a table"
            )
        name = entry.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ManifestError(
                f"[[commands]] entry #{idx} in {source} missing 'name' string"
            )
        description = entry.get("description", "")
        if description is not None and not isinstance(description, str):
            raise ManifestError(
                f"[[commands]] entry #{idx} ({name}) 'description' must be a string"
            )
        template = entry.get("prompt_template", "")
        if template is not None and not isinstance(template, str):
            raise ManifestError(
                f"[[commands]] entry #{idx} ({name}) 'prompt_template' must be a string"
            )
        out.append(
            CommandSpec(
                name=name.strip(),
                description=(description or "").strip(),
                prompt_template=template or "",
            )
        )
    return out


def _parse_themes(
    raw: Any, *, source: str,
) -> list[tuple[str, tuple[tuple[str, str], ...]]]:
    if not raw:
        return []
    if not isinstance(raw, dict):
        raise ManifestError(f"[themes] must be a table of tables in {source}")
    out: list[tuple[str, tuple[tuple[str, str], ...]]] = []
    for theme_name, theme_dict in raw.items():
        if not isinstance(theme_dict, dict):
            raise ManifestError(
                f"[themes.{theme_name}] must be a table in {source}"
            )
        pairs: list[tuple[str, str]] = []
        for key, value in theme_dict.items():
            if not isinstance(key, str):
                raise ManifestError(
                    f"[themes.{theme_name}] keys must be strings"
                )
            if not isinstance(value, str):
                raise ManifestError(
                    f"[themes.{theme_name}].{key} must be a string"
                )
            pairs.append((key, value))
        out.append((str(theme_name), tuple(pairs)))
    return out


def _parse_variables(raw: Any, *, source: str) -> list[tuple[str, str]]:
    if not raw:
        return []
    if not isinstance(raw, dict):
        raise ManifestError(f"[variables] must be a table in {source}")
    out: list[tuple[str, str]] = []
    for key, value in raw.items():
        if not isinstance(key, str):
            raise ManifestError(f"[variables] keys must be strings in {source}")
        if not isinstance(value, str):
            raise ManifestError(
                f"[variables].{key} must be a string in {source}"
            )
        out.append((key, value))
    return out
