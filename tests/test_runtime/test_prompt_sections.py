"""Tests for prompt section builders."""
from __future__ import annotations

from dataclasses import dataclass

from llm_code.runtime.prompt_sections import (
    build_personas_section,
    build_skills_section,
    build_tools_section,
)


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


def test_tools_section_empty_for_none() -> None:
    assert build_tools_section(None) == ""


def test_tools_section_iterable_of_dicts() -> None:
    out = build_tools_section([{"name": "bash", "description": "run shell"}])
    assert "## Available Tools" in out
    assert "**bash**" in out


def test_tools_section_via_list_tools() -> None:
    class Reg:
        def list_tools(self):
            return [_Stub("read", "read files")]

    out = build_tools_section(Reg())
    assert "**read**" in out


def test_skills_section_empty() -> None:
    assert build_skills_section([]) == ""


def test_skills_section_renders() -> None:
    out = build_skills_section([_Stub("xlsx", "spreadsheets")])
    assert "## Available Skills" in out
    assert "xlsx" in out
