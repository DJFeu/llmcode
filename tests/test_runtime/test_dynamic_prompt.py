"""Tests for the dynamic prompt builder (Plan 3)."""
from __future__ import annotations

import pytest

from llm_code.api.types import ToolDefinition
from llm_code.runtime.dynamic_prompt import (
    TOOL_CATEGORIES,
    build_delegation_section,
    classify_tool,
    group_skills_by_category,
)
from llm_code.runtime.skills import Skill


def _tool(name: str, description: str = "") -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=description or f"the {name} tool",
        input_schema={"type": "object", "properties": {}},
    )


def _skill(name: str, tags: tuple[str, ...] = (), description: str = "") -> Skill:
    return Skill(
        name=name,
        description=description or f"{name} guidance",
        content="(body)",
        tags=tags,
    )


# ---------------------------------------------------------------------------
# classify_tool
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name, expected",
    [
        ("read_file", "read"),
        ("Read", "read"),
        ("notebook_read", "read"),
        ("grep_search", "search"),
        ("glob_search", "search"),
        ("write_file", "write"),
        ("edit_file", "write"),
        ("multi_edit", "write"),
        ("notebook_edit", "write"),
        ("Write", "write"),
        ("bash", "exec"),
        ("Bash", "exec"),
        ("lsp_hover", "lsp"),
        ("lsp_goto_definition", "lsp"),
        ("web_fetch", "web"),
        ("web_search", "web"),
        ("WebFetch", "web"),
        ("agent", "agent"),
        ("task_create", "agent"),
        ("totally_unknown_tool", "other"),
    ],
)
def test_classify_tool(name: str, expected: str) -> None:
    assert classify_tool(name) == expected


def test_tool_categories_are_canonical() -> None:
    expected = {"read", "search", "write", "exec", "lsp", "web", "agent", "other"}
    assert set(TOOL_CATEGORIES) == expected


# ---------------------------------------------------------------------------
# group_skills_by_category
# ---------------------------------------------------------------------------


def test_group_skills_uses_first_tag_as_category() -> None:
    s1 = _skill("debug", tags=("debugging", "errors"))
    s2 = _skill("plan", tags=("planning",))
    grouped = group_skills_by_category((s1, s2))
    assert "debugging" in grouped
    assert "planning" in grouped
    assert grouped["debugging"][0].name == "debug"


def test_group_skills_falls_back_to_general_when_no_tags() -> None:
    s = _skill("plain", tags=())
    grouped = group_skills_by_category((s,))
    assert "general" in grouped
    assert grouped["general"][0].name == "plain"


def test_group_skills_preserves_order_within_category() -> None:
    a = _skill("a", tags=("x",))
    b = _skill("b", tags=("x",))
    c = _skill("c", tags=("x",))
    grouped = group_skills_by_category((a, b, c))
    assert [s.name for s in grouped["x"]] == ["a", "b", "c"]


# ---------------------------------------------------------------------------
# build_delegation_section
# ---------------------------------------------------------------------------


def test_build_delegation_section_returns_empty_when_no_tools_or_skills() -> None:
    assert build_delegation_section((), ()) == ""


def test_build_delegation_section_renders_header() -> None:
    out = build_delegation_section((_tool("read_file"),), ())
    assert out.startswith("## Active Capabilities")


def test_section_contains_tool_table_when_tools_present() -> None:
    tools = (
        _tool("read_file", "Read a file"),
        _tool("write_file", "Write a file"),
        _tool("bash", "Run a shell command"),
        _tool("lsp_hover", "Hover info"),
    )
    out = build_delegation_section(tools, ())
    assert "### Tools by Capability" in out
    assert "**read**" in out
    assert "**write**" in out
    assert "**exec**" in out
    assert "**lsp**" in out
    assert "read_file" in out
    assert "write_file" in out


def test_section_contains_key_triggers_when_skills_present() -> None:
    skills = (
        _skill("debugging", tags=("debug",), description="systematic debugging"),
        _skill("brainstorming", tags=("design",), description="explore options"),
    )
    out = build_delegation_section((), skills)
    assert "### Key Triggers" in out
    assert "debugging" in out
    assert "brainstorming" in out


def test_section_contains_category_table_when_skills_present() -> None:
    skills = (
        _skill("debug", tags=("debugging",)),
        _skill("plan", tags=("planning",)),
        _skill("brainstorm", tags=("planning",)),
    )
    out = build_delegation_section((), skills)
    assert "### Skills by Category" in out
    assert "**debugging**" in out
    assert "**planning**" in out
    planning_lines = [
        line for line in out.splitlines()
        if line.strip().startswith("- ") and ("plan" in line or "brainstorm" in line)
    ]
    assert len(planning_lines) >= 2


def test_section_truncates_to_max_tools() -> None:
    tools = tuple(_tool(f"tool_{i}") for i in range(50))
    out = build_delegation_section(tools, (), max_tools=10)
    assert "(+40 more)" in out or "40 more" in out


def test_section_truncates_to_max_skills() -> None:
    skills = tuple(_skill(f"s{i}", tags=("x",)) for i in range(30))
    out = build_delegation_section((), skills, max_skills=5)
    assert "more)" in out


def test_section_includes_tool_description_truncated() -> None:
    long_desc = "X" * 500
    out = build_delegation_section((_tool("read_file", long_desc),), ())
    assert "read_file" in out
    assert "..." in out or "X" * 500 not in out


def test_section_uses_skill_trigger_when_set() -> None:
    s = Skill(
        name="my-skill",
        description="d",
        content="",
        trigger="when user says X",
        tags=("debug",),
    )
    out = build_delegation_section((), (s,))
    assert "when user says X" in out


def test_build_delegation_section_is_deterministic() -> None:
    """Same inputs => byte-identical output (cache-safe)."""
    tools = (
        _tool("read_file", "Read a file"),
        _tool("write_file", "Write a file"),
        _tool("bash", "Run a shell command"),
    )
    skills = (
        _skill("debugging", tags=("debug",)),
        _skill("brainstorming", tags=("planning",)),
    )
    a = build_delegation_section(tools, skills)
    b = build_delegation_section(tools, skills)
    assert a == b


def test_build_delegation_section_is_order_sensitive() -> None:
    a_tools = (_tool("read_file"), _tool("write_file"))
    b_tools = (_tool("write_file"), _tool("read_file"))
    a = build_delegation_section(a_tools, ())
    b = build_delegation_section(b_tools, ())
    assert a != b
    assert "read_file" in a and "read_file" in b
    assert "write_file" in a and "write_file" in b


def test_build_delegation_section_respects_byte_budget() -> None:
    big_desc = "x" * 200
    tools = tuple(_tool(f"tool_{i}", big_desc) for i in range(50))
    skills = tuple(_skill(f"s{i}", tags=("cat",), description=big_desc) for i in range(50))
    out = build_delegation_section(tools, skills, max_bytes=2048)
    assert len(out.encode("utf-8")) <= 2048
    assert "## Active Capabilities" in out


def test_build_delegation_section_does_not_hang_under_tiny_budget() -> None:
    """A pathologically small max_bytes (smaller than header+intro) must not
    cause an infinite loop in the tool-table truncation halving logic."""
    import signal

    tools = tuple(
        _tool(f"tool_{i}", description="x" * 50) for i in range(50)
    )
    skills: tuple[Skill, ...] = ()

    def _alarm(signum, frame):  # type: ignore[no-untyped-def]
        raise TimeoutError("truncation loop did not converge")

    signal.signal(signal.SIGALRM, _alarm)
    signal.alarm(2)
    try:
        out = build_delegation_section(tools, skills, max_bytes=10)
    finally:
        signal.alarm(0)

    assert isinstance(out, str)


def test_build_delegation_section_no_truncation_when_under_budget() -> None:
    out = build_delegation_section(
        (_tool("read_file"),),
        (_skill("debug", tags=("d",)),),
        max_bytes=10_000,
    )
    assert "Tools by Capability" in out
    assert "Key Triggers" in out
