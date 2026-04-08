"""LspImplementationTool tests."""
from __future__ import annotations

from llm_code.lsp.client import Location
from llm_code.lsp.tools import LspImplementationTool


class _FakeClient:
    def __init__(self, locs: list[Location]) -> None:
        self._locs = locs
        self.calls: list[tuple[str, int, int]] = []

    async def go_to_implementation(self, uri: str, line: int, col: int) -> list[Location]:
        self.calls.append((uri, line, col))
        return self._locs


class _FakeManager:
    def __init__(self, client) -> None:
        self._client = client

    def get_client(self, language: str):
        return self._client


def test_implementation_tool_renders_locations(tmp_path) -> None:
    py = tmp_path / "iface.py"
    py.write_text("class Iface: ...")
    locs = [
        Location(file="file:///impl_a.py", line=10, column=4),
        Location(file="file:///impl_b.py", line=20, column=0),
    ]
    tool = LspImplementationTool(_FakeManager(_FakeClient(locs)))
    out = tool.execute({"file": str(py), "line": 0, "column": 6}).output
    assert "impl_a.py:10:4" in out
    assert "impl_b.py:20:0" in out


def test_implementation_tool_no_results(tmp_path) -> None:
    py = tmp_path / "iface.py"
    py.write_text("")
    tool = LspImplementationTool(_FakeManager(_FakeClient([])))
    out = tool.execute({"file": str(py), "line": 0, "column": 0}).output
    assert "no implementation" in out.lower()


def test_implementation_tool_no_client(tmp_path) -> None:
    weird = tmp_path / "x.unknown_ext_zzz"
    weird.write_text("")
    tool = LspImplementationTool(_FakeManager(None))
    result = tool.execute({"file": str(weird), "line": 0, "column": 0})
    assert result.is_error is True


def test_implementation_tool_input_schema() -> None:
    tool = LspImplementationTool(_FakeManager(None))
    assert tool.input_schema["required"] == ["file", "line", "column"]
    assert tool.name == "lsp_implementation"


def test_implementation_tool_is_read_only() -> None:
    tool = LspImplementationTool(_FakeManager(None))
    assert tool.is_read_only({}) is True
    assert tool.is_concurrency_safe({}) is True
