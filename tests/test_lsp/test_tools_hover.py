"""LspHoverTool unit tests using a fake manager + client."""
from __future__ import annotations

from llm_code.lsp.client import Hover
from llm_code.lsp.tools import LspHoverTool


class _FakeClient:
    def __init__(self, hover: Hover) -> None:
        self._hover = hover
        self.calls: list[tuple[str, int, int]] = []

    async def hover(self, file_uri: str, line: int, col: int) -> Hover:
        self.calls.append((file_uri, line, col))
        return self._hover


class _FakeManager:
    def __init__(self, client) -> None:
        self._client = client

    def get_client(self, language: str):
        return self._client


def test_hover_tool_returns_contents_for_known_language(tmp_path) -> None:
    py = tmp_path / "x.py"
    py.write_text("x = 1")
    fake = _FakeClient(Hover(contents="x: int"))
    tool = LspHoverTool(_FakeManager(fake))
    result = tool.execute({"file": str(py), "line": 0, "column": 0})
    assert "x: int" in result.output
    assert result.is_error is False


def test_hover_tool_errors_for_unsupported_language(tmp_path) -> None:
    weird = tmp_path / "x.unknown_ext_zzz"
    weird.write_text("")
    tool = LspHoverTool(_FakeManager(None))
    result = tool.execute({"file": str(weird), "line": 0, "column": 0})
    assert result.is_error is True


def test_hover_tool_handles_empty_response(tmp_path) -> None:
    py = tmp_path / "x.py"
    py.write_text("")
    fake = _FakeClient(Hover(contents=""))
    tool = LspHoverTool(_FakeManager(fake))
    result = tool.execute({"file": str(py), "line": 0, "column": 0})
    assert "no hover" in result.output.lower() or result.output == ""


def test_hover_tool_input_schema_has_required_fields() -> None:
    tool = LspHoverTool(_FakeManager(None))
    schema = tool.input_schema
    assert schema["required"] == ["file", "line", "column"]


def test_hover_tool_is_read_only() -> None:
    tool = LspHoverTool(_FakeManager(None))
    assert tool.is_read_only({}) is True
    assert tool.is_concurrency_safe({}) is True
