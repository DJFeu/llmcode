"""Wiring tests for frontmatter hooks registration on skill load."""
from __future__ import annotations

import inspect

from llm_code.runtime.conversation import ConversationRuntime
from llm_code.runtime.frontmatter_hooks import (
    register_skill_hooks,
    register_skillset_hooks,
)
from llm_code.runtime.skills import Skill


class _FakeHookRunner:
    def __init__(self) -> None:
        self.calls: list = []

    def register(self, event, handler):  # some builtins call .register
        self.calls.append((event, handler))


def test_conversation_init_wires_frontmatter_hooks():
    src = inspect.getsource(ConversationRuntime.__init__)
    assert "register_skillset_hooks" in src


def test_register_skill_hooks_skips_unknown_handler():
    skill = Skill(
        name="demo",
        description="",
        content="",
        auto=True,
        hooks=(("pre_tool_use", "nonexistent_handler"),),
    )
    registered = register_skill_hooks(skill, _FakeHookRunner())
    assert registered == []


def test_register_skillset_hooks_iterates():
    skills_tuple = (
        Skill(name="s1", description="", content="", auto=True,
              hooks=(("pre_tool_use", "no_such"),)),
        Skill(name="s2", description="", content="", auto=False, hooks=()),
    )
    # Should not raise even when handlers unknown
    out = register_skillset_hooks(skills_tuple, _FakeHookRunner())
    assert isinstance(out, list)
