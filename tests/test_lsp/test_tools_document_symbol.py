"""LspDocumentSymbolTool tests."""
from __future__ import annotations

from llm_code.lsp.client import SymbolInfo
from llm_code.lsp.tools import LspDocumentSymbolTool


class _FakeClient:
    def __init__(self, symbols: list[SymbolInfo]) -> None:
        self._symbols = symbols

    async def document_symbol(self, uri: str) -> list[SymbolInfo]:
        return self._symbols


class _FakeManager:
    def __init__(self, client) -> None:
        self._client = client

    def get_client(self, language: str):
        return self._client


def test_document_symbol_tool_renders_one_per_line(tmp_path) -> None:
    py = tmp_path / "x.py"
    py.write_text("")
    syms = [
        SymbolInfo(name="A", kind="class", file=py.as_uri(), line=1, column=0),
        SymbolInfo(name="b", kind="function", file=py.as_uri(), line=5, column=4),
    ]
    tool = LspDocumentSymbolTool(_FakeManager(_FakeClient(syms)))
    out = tool.execute({"file": str(py)}).output
    assert "class A" in out
    assert "function b" in out
    assert out.count("\n") >= 1


def test_document_symbol_tool_empty(tmp_path) -> None:
    py = tmp_path / "x.py"
    py.write_text("")
    tool = LspDocumentSymbolTool(_FakeManager(_FakeClient([])))
    out = tool.execute({"file": str(py)}).output
    assert "no symbols" in out.lower() or out == ""


def test_document_symbol_tool_no_client(tmp_path) -> None:
    weird = tmp_path / "x.unknown_ext_zzz"
    weird.write_text("")
    tool = LspDocumentSymbolTool(_FakeManager(None))
    result = tool.execute({"file": str(weird)})
    assert result.is_error is True
