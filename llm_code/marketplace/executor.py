"""Wave2-5: plugin executor — dynamic loader for plugin-provided tools and skills.

Takes a parsed :class:`PluginManifest` + install directory and turns
declared ``provides_tools`` entries into registered ``Tool`` instances
on the target ``ToolRegistry``, plus optionally feeds any plugin
skills into a ``SkillRouter``.

Two hard contracts this module honors:

1. **Rollback on conflict.** If any entry fails — missing module,
   unimportable class, duplicate tool name — every tool that was
   already registered by this load call is unregistered before the
   exception propagates, so the registry stays in the state it was
   in before ``load_plugin`` started.

2. **sys.path hygiene.** The plugin install directory is added to
   ``sys.path`` for the duration of the import, then removed in a
   ``finally`` block. Plugins that share module names with the host
   still break ``importlib``'s module cache, but that's a plugin
   author bug, not an executor bug — the log messages name the
   plugin so the misconfiguration is traceable.

The executor deliberately does not do MCP server or hook wiring —
those already live in the installer + TUI code. This module only
fills the gap for *Python tool modules*, which had no executor at
all before wave2-5.
"""
from __future__ import annotations

import importlib
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from llm_code.marketplace.plugin import PluginManifest

if TYPE_CHECKING:
    from llm_code.runtime.skill_router import SkillRouter
    from llm_code.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


class PluginLoadError(Exception):
    """Raised when a plugin cannot be loaded.

    Carries the plugin name + the offending entry so a log reader
    can tell which plugin broke without stack-trace diving.
    """

    def __init__(self, plugin_name: str, entry: str, reason: str) -> None:
        self.plugin_name = plugin_name
        self.entry = entry
        self.reason = reason
        super().__init__(
            f"plugin '{plugin_name}' load failed at entry {entry!r}: {reason}"
        )


class PluginConflictError(PluginLoadError):
    """Raised when a plugin's tool name clashes with an existing tool.

    Distinct subclass so callers can catch it specifically and offer
    a --force override. The rollback has already run by the time
    this is raised — the registry is in its pre-load state.
    """


@dataclass
class LoadedPlugin:
    """Runtime handle for a successfully loaded plugin.

    Holds the manifest plus the names of every tool and skill the
    executor registered for this plugin. Used by ``unload_plugin``
    to reverse the load cleanly when the plugin is disabled.
    """

    manifest: PluginManifest
    install_path: Path
    tool_names: list[str] = field(default_factory=list)
    skill_names: list[str] = field(default_factory=list)


def _resolve_entry(entry: str) -> tuple[str, str]:
    """Parse a ``"module.path:ClassName"`` entry.

    Returns ``(module_path, class_name)``. Accepts a few legacy
    shapes that the marketplace fixture tests rely on: the separator
    may be ``:`` (preferred) or ``.`` in which case the last dotted
    segment is treated as the class name.

    Raises ``ValueError`` on empty string or missing class name so
    the caller's rollback path kicks in with a clear reason.
    """
    entry = entry.strip()
    if not entry:
        raise ValueError("empty entry")
    if ":" in entry:
        module_path, _, class_name = entry.rpartition(":")
    else:
        module_path, _, class_name = entry.rpartition(".")
    if not module_path or not class_name:
        raise ValueError(
            f"cannot parse entry {entry!r}: expected 'module.path:ClassName'"
        )
    return module_path, class_name


def load_plugin(
    manifest: PluginManifest,
    install_path: Path,
    *,
    tool_registry: "ToolRegistry",
    skill_router: "SkillRouter | None" = None,
    force: bool = False,
) -> LoadedPlugin:
    """Wave2-5: load a plugin's tools (and optionally skills) into the runtime.

    Parameters
    ----------
    manifest
        The parsed manifest (typically from ``PluginManifest.from_path``).
    install_path
        Directory the plugin was installed to. Added to ``sys.path``
        for the duration of the import so relative modules resolve.
    tool_registry
        The target registry. Each ``provides_tools`` entry is
        instantiated and registered here.
    skill_router
        Optional. When provided, any loaded plugin skill objects are
        appended via ``SkillRouter.add_skill``. Pass ``None`` to
        defer skill wiring (the existing TUI ``_reload_skills`` path
        still handles markdown skills).
    force
        When True, a tool name conflict causes the existing tool to
        be unregistered before the plugin tool takes its slot. The
        caller is responsible for the UX of surfacing the override.

    Returns
    -------
    LoadedPlugin
        A handle holding the manifest + the names of every tool and
        skill that was registered, so ``unload_plugin`` can reverse
        the load cleanly.

    Raises
    ------
    PluginConflictError
        A declared tool has the same name as an existing tool and
        ``force`` is False. Rollback has already run.
    PluginLoadError
        Any other load failure: unparseable entry, missing module,
        missing class, instantiation error. Rollback has already run.
    """
    handle = LoadedPlugin(manifest=manifest, install_path=install_path)

    # Wave2-5 follow-up (#5): permissions enforcement. If the
    # manifest declares ``permissions``, log which capabilities the
    # plugin requests. For now this is advisory (log + hook) — a
    # full sandbox that actually blocks network/fs/subprocess access
    # would require OS-level isolation (e.g. seccomp, containers)
    # which is out of scope. The advisory gate lets users make an
    # informed decision before enabling a plugin.
    if manifest.permissions:
        logger.info(
            "plugin %s declares permissions: %s",
            manifest.name,
            ", ".join(
                f"{k}={v}" for k, v in manifest.permissions.items()
            ),
        )
        # Block plugins that request subprocess or fs_write unless
        # the caller explicitly passed force=True. Network is
        # allowed by default because most tools need it.
        dangerous_caps = {
            k for k, v in manifest.permissions.items()
            if v is True and k in ("subprocess", "fs_write")
        }
        if dangerous_caps and not force:
            raise PluginLoadError(
                manifest.name,
                "permissions",
                f"plugin requests dangerous capabilities: "
                f"{', '.join(sorted(dangerous_caps))}. Use --force "
                f"or /plugin install --force to override.",
            )

    if not manifest.provides_tools and not manifest.skills:
        # Nothing to do — but still return a valid handle so the
        # caller can track the empty load in its plugin table.
        logger.debug("plugin %s has no provides_tools or skills", manifest.name)
        return handle

    path_str = str(install_path)
    added_path = False
    if manifest.provides_tools and path_str not in sys.path:
        sys.path.insert(0, path_str)
        added_path = True

    try:
        for entry in manifest.provides_tools:
            try:
                module_path, class_name = _resolve_entry(entry)
            except ValueError as exc:
                _rollback(handle, tool_registry)
                raise PluginLoadError(manifest.name, entry, str(exc)) from exc

            try:
                module = importlib.import_module(module_path)
            except ImportError as exc:
                _rollback(handle, tool_registry)
                raise PluginLoadError(
                    manifest.name, entry, f"import failed: {exc}"
                ) from exc

            tool_cls = getattr(module, class_name, None)
            if tool_cls is None:
                _rollback(handle, tool_registry)
                raise PluginLoadError(
                    manifest.name, entry,
                    f"class {class_name!r} not found in module {module_path!r}",
                )

            try:
                tool = tool_cls()
            except Exception as exc:  # noqa: BLE001 — any ctor failure is fatal for this entry
                _rollback(handle, tool_registry)
                raise PluginLoadError(
                    manifest.name, entry, f"instantiation failed: {exc}"
                ) from exc

            tool_name = getattr(tool, "name", None)
            if not tool_name:
                _rollback(handle, tool_registry)
                raise PluginLoadError(
                    manifest.name, entry,
                    "loaded object has no .name attribute",
                )

            existing = tool_registry.get(tool_name)
            if existing is not None:
                if not force:
                    _rollback(handle, tool_registry)
                    raise PluginConflictError(
                        manifest.name, entry,
                        f"tool name {tool_name!r} already registered",
                    )
                tool_registry.unregister(tool_name)

            try:
                tool_registry.register(tool)
            except ValueError as exc:
                # Lost a race with another registration. Treat as
                # conflict to give the same user-facing behavior.
                _rollback(handle, tool_registry)
                raise PluginConflictError(
                    manifest.name, entry, str(exc)
                ) from exc

            handle.tool_names.append(tool_name)
            logger.info(
                "plugin %s: registered tool %s", manifest.name, tool_name,
            )

        # Skill wiring is optional and best-effort — a failure here
        # does not roll back the tools (tools were already registered
        # successfully and are valuable on their own).
        if skill_router is not None and manifest.skills:
            # manifest.skills can be a str (single file) or tuple
            # (multiple files). For wave2-5 we defer actual skill
            # file parsing to the existing loader — the executor's
            # job is to pass the resolved skill objects through, not
            # to re-invent skill markdown parsing. A follow-up that
            # wires this to /plugin install will supply pre-parsed
            # skill objects.
            logger.debug(
                "plugin %s declares skills but skill-object resolution "
                "is handled by the caller's skill loader", manifest.name,
            )
    finally:
        if added_path:
            try:
                sys.path.remove(path_str)
            except ValueError:
                pass  # Another loader removed it; harmless.

    return handle


def unload_plugin(
    handle: LoadedPlugin,
    *,
    tool_registry: "ToolRegistry",
    skill_router: "SkillRouter | None" = None,
) -> None:
    """Wave2-5: reverse a successful ``load_plugin`` call.

    Un-registers every tool and skill the handle recorded. Safe to
    call on a handle that was partially loaded (the executor's
    rollback uses the same name list).
    """
    for tool_name in handle.tool_names:
        removed = tool_registry.unregister(tool_name)
        if removed:
            logger.info(
                "plugin %s: unregistered tool %s",
                handle.manifest.name, tool_name,
            )
    handle.tool_names.clear()

    if skill_router is not None:
        for skill_name in handle.skill_names:
            skill_router.remove_skill(skill_name)
        handle.skill_names.clear()


def _rollback(handle: LoadedPlugin, tool_registry: "ToolRegistry") -> None:
    """Remove every tool this load call already registered."""
    for tool_name in handle.tool_names:
        tool_registry.unregister(tool_name)
    handle.tool_names.clear()
