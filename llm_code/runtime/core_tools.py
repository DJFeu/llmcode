"""Core-tool registration helper.

Relocated from ``tui/app.py`` as part of M11 cutover. The TUI used to
own this helper because it lived next to the app that called it, but
``_register_core_tools`` has zero widget dependencies — it's a pure
``(registry, config) -> None`` mutation that registers the collaborator-
free built-in tool set (read/write/bash/search/git/etc.).

Both ``AppState.from_config`` (M10.3) and
``llm_code.cli.oneshot.run_quick_mode`` (headless one-shot path) share
this helper so they exercise the exact same tool set.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from llm_code.runtime.config import RuntimeConfig
    from llm_code.tools.registry import ToolRegistry


__all__ = ["register_core_tools", "make_sandbox"]


def register_core_tools(
    registry: "ToolRegistry",
    config: "RuntimeConfig",
    *,
    lifecycle=None,
) -> None:
    """Register the collaborator-free core tool set into ``registry``.

    Shared between the REPL boot path (via AppState.from_config) and
    headless callers (``run_quick_mode``) so both exercise the same
    file / shell / search / git tool set. Tools that depend on
    instance-scoped collaborators (MemoryStore, SkillSet, SwarmManager,
    IDEBridge, LspManager, etc.) are intentionally NOT registered here
    — the boot path registers those separately after this helper runs.

    F5-wire-2: when ``lifecycle`` is supplied, the sandbox instance
    handed to BashTool is registered on the manager so session teardown
    can close it. ``None`` (default) keeps the pre-wire behaviour.
    """
    from llm_code.tools.bash import BashTool
    from llm_code.tools.builtin import get_builtin_tools

    try:
        from llm_code.runtime.provider_routing import resolve_provider_target
        base_url = resolve_provider_target(config).base_url
    except Exception:
        base_url = config.provider_base_url or ""
    is_local = any(
        h in base_url
        for h in (
            "localhost", "127.0.0.1", "0.0.0.0",
            "192.168.", "10.", "172.",
        )
    )
    # 0 = no timeout for local models (vLLM can take minutes on the
    # first bash call while the model warms up). Remote providers get
    # a 30-second cap so a stuck shell doesn't wedge the REPL.
    bash_timeout = 0 if is_local else 30

    bash_sandbox = make_sandbox(config)
    if lifecycle is not None and bash_sandbox is not None:
        try:
            lifecycle.register(bash_sandbox)
        except Exception:
            pass  # registration is best-effort; never mask tool setup

    bash_kwargs = dict(
        default_timeout=bash_timeout,
        compress_output=config.output_compression,
        sandbox=bash_sandbox,
    )

    for name, cls in get_builtin_tools().items():
        try:
            if cls is BashTool:
                registry.register(cls(**bash_kwargs))
            else:
                registry.register(cls())
        except ValueError:
            # Duplicate name — a later caller will override, skip.
            pass


def make_sandbox(config: "RuntimeConfig"):
    """Create a ``DockerSandbox`` if one is configured, else None."""
    try:
        sandbox_cfg = getattr(config, "sandbox", None)
        if sandbox_cfg is not None and getattr(sandbox_cfg, "enabled", False):
            from llm_code.tools.sandbox import DockerSandbox
            return DockerSandbox(sandbox_cfg)
    except Exception:
        pass
    return None


# Backwards-compat aliases for the v1.x underscore names that any
# downstream caller might still import directly. Will be removed when
# all call sites are confirmed to use the public names.
_register_core_tools = register_core_tools
_make_sandbox = make_sandbox
