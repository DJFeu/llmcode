"""LspClient call hierarchy tests."""
from __future__ import annotations

from typing import Any

import pytest

from llm_code.lsp.client import CallHierarchyItem, LspClient, LspTransport


class FakeTransport(LspTransport):
    def __init__(self, queue: list[Any]) -> None:
        self._queue = queue
        self.sent: list[dict[str, Any]] = []

    async def start(self) -> None: ...
    async def close(self) -> None: ...

    async def send_message(self, m: dict[str, Any]) -> None:
        self.sent.append(m)

    async def receive_message(self) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": self.sent[-1]["id"], "result": self._queue.pop(0)}


def _hierarchy_item(name: str = "foo", uri: str = "file:///x.py", line: int = 1) -> dict:
    return {
        "name": name,
        "kind": 12,
        "uri": uri,
        "range": {"start": {"line": line, "character": 0}, "end": {"line": line, "character": 4}},
        "selectionRange": {"start": {"line": line, "character": 0}, "end": {"line": line, "character": 4}},
    }


@pytest.mark.asyncio
async def test_prepare_call_hierarchy_parses_items() -> None:
    response = [_hierarchy_item("foo", "file:///x.py", 1)]
    client = LspClient(FakeTransport([response]))
    items = await client.prepare_call_hierarchy("file:///x.py", 1, 0)
    assert len(items) == 1
    assert isinstance(items[0], CallHierarchyItem)
    assert items[0].name == "foo"
    assert items[0].file == "file:///x.py"
    assert items[0].line == 1
    assert items[0].kind == "function"


@pytest.mark.asyncio
async def test_prepare_call_hierarchy_returns_empty_on_null() -> None:
    client = LspClient(FakeTransport([None]))
    items = await client.prepare_call_hierarchy("file:///x.py", 0, 0)
    assert items == []


@pytest.mark.asyncio
async def test_incoming_calls_returns_caller_items() -> None:
    item = CallHierarchyItem(
        name="foo", kind="function", file="file:///x.py", line=1, column=0
    )
    response = [
        {
            "from": _hierarchy_item("caller_a", "file:///a.py", 5),
            "fromRanges": [
                {"start": {"line": 7, "character": 4}, "end": {"line": 7, "character": 7}}
            ],
        },
        {
            "from": _hierarchy_item("caller_b", "file:///b.py", 9),
            "fromRanges": [],
        },
    ]
    client = LspClient(FakeTransport([response]))
    callers = await client.incoming_calls(item)
    names = {c.name for c in callers}
    assert {"caller_a", "caller_b"} <= names


@pytest.mark.asyncio
async def test_outgoing_calls_returns_callee_items() -> None:
    item = CallHierarchyItem(
        name="foo", kind="function", file="file:///x.py", line=1, column=0
    )
    response = [
        {
            "to": _hierarchy_item("callee_a", "file:///a.py", 11),
            "fromRanges": [
                {"start": {"line": 2, "character": 4}, "end": {"line": 2, "character": 12}}
            ],
        },
    ]
    client = LspClient(FakeTransport([response]))
    callees = await client.outgoing_calls(item)
    assert len(callees) == 1
    assert callees[0].name == "callee_a"
    assert callees[0].file == "file:///a.py"


@pytest.mark.asyncio
async def test_prepare_call_hierarchy_sends_correct_payload() -> None:
    transport = FakeTransport([[]])
    client = LspClient(transport)
    await client.prepare_call_hierarchy("file:///x.py", 5, 6)
    sent = transport.sent[-1]
    assert sent["method"] == "textDocument/prepareCallHierarchy"
    assert sent["params"]["position"] == {"line": 5, "character": 6}


@pytest.mark.asyncio
async def test_incoming_calls_sends_full_item() -> None:
    item = CallHierarchyItem(
        name="foo", kind="function", file="file:///x.py", line=1, column=0
    )
    transport = FakeTransport([[]])
    client = LspClient(transport)
    await client.incoming_calls(item)
    sent = transport.sent[-1]
    assert sent["method"] == "callHierarchy/incomingCalls"
    item_payload = sent["params"]["item"]
    assert item_payload["name"] == "foo"
    assert item_payload["uri"] == "file:///x.py"
    assert item_payload["kind"] == 12
