"""Tests for prompt section builders."""
from __future__ import annotations

from dataclasses import dataclass

from llm_code.runtime.prompt_sections import build_personas_section


@dataclass
class _Stub:
    name: str
    description: str = ""


def test_personas_section_empty() -> None:
    assert build_personas_section({}) == ""


def test_personas_section_renders_names() -> None:
    out = build_personas_section({"oracle": _Stub("oracle", "wise")})
    assert "## Available Personas" in out
    assert "**oracle**" in out
    assert "wise" in out
