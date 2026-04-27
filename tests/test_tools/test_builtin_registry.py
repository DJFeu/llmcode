"""Tests for the centralized built-in tool registry."""
from __future__ import annotations


def test_builtin_tools_returns_dict():
    from llm_code.tools.builtin import get_builtin_tools

    tools = get_builtin_tools()
    assert isinstance(tools, dict)
    assert len(tools) >= 10
    assert "read_file" in tools
    assert "bash" in tools
    assert "git_status" in tools


def test_builtin_tools_are_tool_subclasses():
    from llm_code.tools.base import Tool
    from llm_code.tools.builtin import get_builtin_tools

    for name, cls in get_builtin_tools().items():
        assert issubclass(cls, Tool), f"{name} is not a Tool subclass"


def test_builtin_tools_names_match_keys():
    """Each tool's .name property should match the dict key."""
    from llm_code.tools.builtin import get_builtin_tools

    for key, cls in get_builtin_tools().items():
        instance = cls.__new__(cls)
        assert instance.name == key, f"key={key!r} but .name={instance.name!r}"


def test_builtin_tools_expected_count():
    """Sanity check: we expect 18 core tools (10 base + rerank + 7 git).

    v2.8.0 M1 added the ``rerank`` tool. v2.8.0 M5 will add ``research``
    in wave 3 and bump this again.
    """
    from llm_code.tools.builtin import get_builtin_tools

    tools = get_builtin_tools()
    assert len(tools) == 18
