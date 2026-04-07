"""Tests for llm_code.tui.tool_render.render_tool_args."""
from __future__ import annotations

from llm_code.tui.tool_render import render_tool_args


def test_read_file_dict() -> None:
    out = render_tool_args("read_file", {"file_path": "/tmp/a.txt"})
    assert out == "/tmp/a.txt"


def test_read_file_string_dict_repr() -> None:
    out = render_tool_args("read_file", "{'file_path': '/tmp/a.txt'}")
    assert out == "/tmp/a.txt"


def test_read_file_long_path_basenames() -> None:
    long = "/a/" + "b" * 200 + "/file.txt"
    out = render_tool_args("read_file", {"file_path": long})
    assert out == "file.txt"


def test_edit_file_uses_path() -> None:
    out = render_tool_args("edit_file", {"file_path": "/x/y.py"})
    assert "/x/y.py" in out


def test_write_file_uses_path() -> None:
    out = render_tool_args("write_file", {"file_path": "/x/y.py"})
    assert "/x/y.py" in out


def test_notebook_read_edit() -> None:
    assert "/nb.ipynb" in render_tool_args("notebook_read", {"notebook_path": "/nb.ipynb"})
    assert "/nb.ipynb" in render_tool_args("notebook_edit", {"notebook_path": "/nb.ipynb"})


def test_bash_dict() -> None:
    out = render_tool_args("bash", {"command": "ls -la"})
    assert out.startswith("$ ls -la")


def test_bash_truncates_at_60() -> None:
    long_cmd = "echo " + "x" * 200
    out = render_tool_args("bash", {"command": long_cmd})
    # "$ " prefix + truncated 60-char body
    assert out.startswith("$ ")
    assert len(out) <= 62 + 1  # a little slack
    assert out.endswith("…")


def test_glob_search() -> None:
    out = render_tool_args("glob_search", {"pattern": "**/*.py"})
    assert out == "**/*.py"


def test_grep_search_with_path() -> None:
    out = render_tool_args("grep_search", {"pattern": "foo", "path": "src"})
    assert out == "foo in src"


def test_grep_search_without_path() -> None:
    out = render_tool_args("grep_search", {"pattern": "foo"})
    assert out == "foo"


def test_web_fetch_host_and_path() -> None:
    out = render_tool_args("web_fetch", {"url": "https://example.com/docs/page"})
    assert "example.com" in out
    assert "/docs/page" in out


def test_web_search() -> None:
    out = render_tool_args("web_search", {"query": "python asyncio"})
    assert out == "python asyncio"


def test_unknown_tool_fallback() -> None:
    out = render_tool_args("mystery_tool", {"foo": "bar"})
    assert len(out) <= 60


def test_malformed_string_does_not_crash() -> None:
    out = render_tool_args("read_file", "{not valid python dict")
    assert isinstance(out, str)


def test_none_like_input() -> None:
    out = render_tool_args("read_file", "")
    assert isinstance(out, str)
