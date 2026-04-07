"""Built-in Python hook implementations ported from oh-my-opencode.

Each submodule exposes a ``register(hook_runner)`` function that subscribes
itself to the appropriate event(s) on the given :class:`HookRunner` instance.

Use :func:`register_all` to enable every builtin, or :func:`register_named`
to enable a specific subset (driven by ``config.hooks.builtin_enabled``).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from . import (
    auto_commit_offer,
    auto_format,
    auto_lint,
    context_recovery,
    intent_classifier,
)

if TYPE_CHECKING:
    from llm_code.runtime.hooks import HookRunner

BUILTIN_HOOKS = {
    "auto_format": auto_format,
    "auto_lint": auto_lint,
    "intent_classifier": intent_classifier,
    "context_recovery": context_recovery,
    "auto_commit_offer": auto_commit_offer,
}


def register_all(hook_runner: "HookRunner") -> None:
    for module in BUILTIN_HOOKS.values():
        module.register(hook_runner)


def register_named(hook_runner: "HookRunner", names: tuple[str, ...]) -> list[str]:
    """Register only the named builtins. Returns the names that were registered."""
    registered: list[str] = []
    for name in names:
        module = BUILTIN_HOOKS.get(name)
        if module is None:
            continue
        module.register(hook_runner)
        registered.append(name)
    return registered


__all__ = ["BUILTIN_HOOKS", "register_all", "register_named"]
