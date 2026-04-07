"""Register hooks declared in skill/persona frontmatter onto a HookRunner.

Skills may declare lifecycle hooks in their YAML frontmatter::

    ---
    name: my-skill
    hooks:
      pre_tool_use: auto_format
      post_tool_use: auto_lint
      user_prompt_submit: intent_classifier
    ---

The values must reference *names* of builtins in
:mod:`llm_code.runtime.builtin_hooks` — inline code is intentionally not
supported for security. Unknown handler names are logged and skipped.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from llm_code.runtime.builtin_hooks import BUILTIN_HOOKS

if TYPE_CHECKING:
    from llm_code.runtime.hooks import HookRunner
    from llm_code.runtime.skills import Skill

_logger = logging.getLogger(__name__)


def register_skill_hooks(skill: "Skill", hook_runner: "HookRunner") -> list[str]:
    """Register *skill*'s frontmatter hooks onto *hook_runner*.

    Returns the list of ``(event, handler_name)`` pairs that were actually
    registered; unknown handlers are logged and omitted.
    """
    registered: list[str] = []
    for event, handler_name in skill.hooks:
        module = BUILTIN_HOOKS.get(handler_name)
        if module is None:
            _logger.warning(
                "frontmatter_hooks: skill %r references unknown hook %r (skipped)",
                skill.name,
                handler_name,
            )
            continue
        try:
            module.register(hook_runner)
            registered.append(f"{event}:{handler_name}")
        except Exception as exc:  # pragma: no cover - defensive
            _logger.warning(
                "frontmatter_hooks: skill %r failed to register %r: %s",
                skill.name,
                handler_name,
                exc,
            )
    return registered


def register_skillset_hooks(skills, hook_runner: "HookRunner") -> list[str]:
    """Register frontmatter hooks for every skill in an iterable."""
    out: list[str] = []
    for skill in skills:
        out.extend(register_skill_hooks(skill, hook_runner))
    return out
