"""LspWorkspaceSymbolTool tests."""
from __future__ import annotations

from llm_code.lsp.client import SymbolInfo
from llm_code.lsp.tools import LspWorkspaceSymbolTool


class _FakeClient:
    def __init__(self, syms: list[SymbolInfo]) -> None:
        self._syms = syms
        self.queries: list[str] = []

    async def workspace_symbol(self, query: str) -> list[SymbolInfo]:
        self.queries.append(query)
        return self._syms


class _FakeManager:
    def __init__(self, client) -> None:
        self._client = client
        self._clients = {"python": client} if client else {}

    def get_client(self, language: str):
        return self._client

    def any_client(self):
        return self._client


def test_workspace_symbol_tool_aggregates_results() -> None:
    syms = [
        SymbolInfo(name="Foo", kind="class", file="file:///a.py", line=0, column=0),
        SymbolInfo(name="Foo.bar", kind="method", file="file:///a.py", line=2, column=4),
    ]
    tool = LspWorkspaceSymbolTool(_FakeManager(_FakeClient(syms)))
    out = tool.execute({"query": "Foo"}).output
    assert "Foo" in out
    assert "/a.py" in out


def test_workspace_symbol_tool_no_active_servers() -> None:
    tool = LspWorkspaceSymbolTool(_FakeManager(None))
    result = tool.execute({"query": "Foo"})
    assert result.is_error is True
    assert "no lsp" in result.output.lower()


def test_workspace_symbol_tool_empty_result() -> None:
    tool = LspWorkspaceSymbolTool(_FakeManager(_FakeClient([])))
    result = tool.execute({"query": "Foo"})
    assert "no symbols" in result.output.lower()


def test_workspace_symbol_rejects_empty_query() -> None:
    tool = LspWorkspaceSymbolTool(_FakeManager(_FakeClient([])))
    result = tool.execute({"query": ""})
    assert result.is_error is True
    assert "empty" in result.output.lower()


def test_workspace_symbol_rejects_whitespace_query() -> None:
    tool = LspWorkspaceSymbolTool(_FakeManager(_FakeClient([])))
    result = tool.execute({"query": "   "})
    assert result.is_error is True


def test_workspace_symbol_caps_results() -> None:
    syms = [
        SymbolInfo(name=f"sym_{i}", kind="function", file="file:///x.py", line=i, column=0)
        for i in range(500)
    ]
    tool = LspWorkspaceSymbolTool(_FakeManager(_FakeClient(syms)))
    result = tool.execute({"query": "sym"})
    assert result.is_error is False
    assert "sym_199" in result.output
    assert "sym_200" not in result.output
    assert "(+300 more)" in result.output
