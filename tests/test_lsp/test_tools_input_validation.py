"""LSP tools must reject invalid inputs cleanly via ToolResult.is_error=True."""
from __future__ import annotations


from llm_code.lsp.tools import LspHoverTool, _validate_lsp_path


def test_validate_rejects_relative_path() -> None:
    err = _validate_lsp_path("relative/path.py", line=0, column=0)
    assert err is not None
    assert "absolute" in err.lower()


def test_validate_rejects_missing_file(tmp_path) -> None:
    err = _validate_lsp_path(str(tmp_path / "nope.py"), line=0, column=0)
    assert err is not None
    assert "exist" in err.lower()


def test_validate_rejects_negative_line(tmp_path) -> None:
    f = tmp_path / "x.py"
    f.write_text("x = 1")
    err = _validate_lsp_path(str(f), line=-1, column=0)
    assert err is not None
    assert "line" in err.lower()


def test_validate_rejects_negative_column(tmp_path) -> None:
    f = tmp_path / "x.py"
    f.write_text("x = 1")
    err = _validate_lsp_path(str(f), line=0, column=-3)
    assert err is not None
    assert "column" in err.lower()


def test_validate_accepts_valid_input(tmp_path) -> None:
    f = tmp_path / "x.py"
    f.write_text("x = 1")
    assert _validate_lsp_path(str(f), line=0, column=0) is None


def test_hover_tool_relative_path_returns_error() -> None:
    tool = LspHoverTool(manager=None)  # type: ignore[arg-type]
    result = tool.execute({"file": "rel/path.py", "line": 0, "column": 0})
    assert result.is_error is True
