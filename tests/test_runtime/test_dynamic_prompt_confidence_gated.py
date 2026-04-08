"""dynamic_prompt: when called with low_confidence=True, the `### Key Triggers`
block must be suppressed. `### Skills by Category` is still rendered."""
from __future__ import annotations

from llm_code.runtime.dynamic_prompt import build_delegation_section

from tests.test_runtime.test_dynamic_prompt import _tool, _skill


def test_low_confidence_suppresses_key_triggers() -> None:
    tools = (_tool("read_file"),)
    skills = (_skill("brainstorming", tags=("explore",), description="Explore ideas"),)
    out = build_delegation_section(tools, skills, low_confidence=True)
    assert "Key Triggers" not in out
    assert "Skills by Category" in out or "brainstorming" in out


def test_high_confidence_still_renders_key_triggers() -> None:
    tools = (_tool("read_file"),)
    skills = (_skill("brainstorming", tags=("explore",), description="Explore ideas"),)
    out = build_delegation_section(tools, skills, low_confidence=False)
    assert "Key Triggers" in out


def test_default_is_high_confidence_for_backwards_compat() -> None:
    tools = (_tool("read_file"),)
    skills = (_skill("brainstorming", tags=("explore",), description="Explore ideas"),)
    out = build_delegation_section(tools, skills)
    assert "Key Triggers" in out
