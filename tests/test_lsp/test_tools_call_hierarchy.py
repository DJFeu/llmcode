"""LspCallHierarchyTool tests."""
from __future__ import annotations

import pytest

from llm_code.lsp.client import CallHierarchyItem
from llm_code.lsp.tools import LspCallHierarchyTool


class _FakeClient:
    def __init__(
        self,
        prepare_result: list[CallHierarchyItem],
        incoming: list[CallHierarchyItem] | None = None,
        outgoing: list[CallHierarchyItem] | None = None,
    ) -> None:
        self._prepare = prepare_result
        self._incoming = incoming or []
        self._outgoing = outgoing or []
        self.prepare_calls = 0
        self.incoming_calls_count = 0
        self.outgoing_calls_count = 0

    async def prepare_call_hierarchy(self, uri: str, line: int, col: int):
        self.prepare_calls += 1
        return self._prepare

    async def incoming_calls(self, item: CallHierarchyItem):
        self.incoming_calls_count += 1
        return self._incoming

    async def outgoing_calls(self, item: CallHierarchyItem):
        self.outgoing_calls_count += 1
        return self._outgoing


class _FakeManager:
    def __init__(self, client) -> None:
        self._client = client

    def get_client(self, language: str):
        return self._client


def _item(name: str, line: int = 0) -> CallHierarchyItem:
    return CallHierarchyItem(
        name=name, kind="function", file="file:///x.py", line=line, column=0
    )


@pytest.fixture
def py_file(tmp_path):
    py = tmp_path / "x.py"
    py.write_text("def foo(): pass")
    return py


def test_call_hierarchy_incoming_only(py_file) -> None:
    client = _FakeClient(
        prepare_result=[_item("foo")],
        incoming=[_item("caller_a", line=10), _item("caller_b", line=20)],
    )
    tool = LspCallHierarchyTool(_FakeManager(client))
    out = tool.execute(
        {"file": str(py_file), "line": 0, "column": 4, "direction": "incoming"}
    ).output
    assert "Incoming" in out
    assert "caller_a" in out and "caller_b" in out
    assert "Outgoing" not in out
    assert client.outgoing_calls_count == 0


def test_call_hierarchy_outgoing_only(py_file) -> None:
    client = _FakeClient(
        prepare_result=[_item("foo")],
        outgoing=[_item("callee_a", line=99)],
    )
    tool = LspCallHierarchyTool(_FakeManager(client))
    out = tool.execute(
        {"file": str(py_file), "line": 0, "column": 4, "direction": "outgoing"}
    ).output
    assert "Outgoing" in out
    assert "callee_a" in out
    assert "Incoming" not in out
    assert client.incoming_calls_count == 0


def test_call_hierarchy_both_directions(py_file) -> None:
    client = _FakeClient(
        prepare_result=[_item("foo")],
        incoming=[_item("caller", line=5)],
        outgoing=[_item("callee", line=15)],
    )
    tool = LspCallHierarchyTool(_FakeManager(client))
    out = tool.execute(
        {"file": str(py_file), "line": 0, "column": 4, "direction": "both"}
    ).output
    assert "Incoming" in out
    assert "Outgoing" in out
    assert "caller" in out and "callee" in out


def test_call_hierarchy_default_direction_is_both(py_file) -> None:
    client = _FakeClient(
        prepare_result=[_item("foo")],
        incoming=[_item("caller")],
        outgoing=[_item("callee")],
    )
    tool = LspCallHierarchyTool(_FakeManager(client))
    out = tool.execute({"file": str(py_file), "line": 0, "column": 4}).output
    assert "Incoming" in out
    assert "Outgoing" in out


def test_call_hierarchy_no_symbol_at_position(py_file) -> None:
    client = _FakeClient(prepare_result=[])
    tool = LspCallHierarchyTool(_FakeManager(client))
    result = tool.execute(
        {"file": str(py_file), "line": 0, "column": 4, "direction": "both"}
    )
    assert "no symbol" in result.output.lower()


def test_call_hierarchy_no_callers_or_callees(py_file) -> None:
    client = _FakeClient(prepare_result=[_item("foo")])
    tool = LspCallHierarchyTool(_FakeManager(client))
    out = tool.execute(
        {"file": str(py_file), "line": 0, "column": 4, "direction": "both"}
    ).output
    assert "no incoming" in out.lower() or "(none)" in out.lower()
    assert "no outgoing" in out.lower() or "(none)" in out.lower()


def test_call_hierarchy_invalid_direction(py_file) -> None:
    client = _FakeClient(prepare_result=[_item("foo")])
    tool = LspCallHierarchyTool(_FakeManager(client))
    result = tool.execute(
        {"file": str(py_file), "line": 0, "column": 4, "direction": "sideways"}
    )
    assert result.is_error is True
    assert "direction" in result.output.lower()


def test_call_hierarchy_no_client(tmp_path) -> None:
    weird = tmp_path / "x.unknown_ext_zzz"
    weird.write_text("")
    tool = LspCallHierarchyTool(_FakeManager(None))
    result = tool.execute(
        {"file": str(weird), "line": 0, "column": 0, "direction": "both"}
    )
    assert result.is_error is True


def test_call_hierarchy_input_schema() -> None:
    tool = LspCallHierarchyTool(_FakeManager(None))
    schema = tool.input_schema
    assert "direction" in schema["properties"]
    assert schema["properties"]["direction"]["enum"] == ["incoming", "outgoing", "both"]
    assert schema["required"] == ["file", "line", "column"]
