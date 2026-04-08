"""LspClient.go_to_implementation() tests."""
from __future__ import annotations

from typing import Any

import pytest

from llm_code.lsp.client import Location, LspClient, LspTransport


class FakeTransport(LspTransport):
    def __init__(self, response: Any) -> None:
        self._response = response
        self.sent: list[dict[str, Any]] = []

    async def start(self) -> None: ...
    async def close(self) -> None: ...

    async def send_message(self, m: dict[str, Any]) -> None:
        self.sent.append(m)

    async def receive_message(self) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": self.sent[-1]["id"], "result": self._response}


@pytest.mark.asyncio
async def test_go_to_implementation_parses_single_location() -> None:
    response = {
        "uri": "file:///impl.py",
        "range": {"start": {"line": 12, "character": 4}, "end": {"line": 20, "character": 0}},
    }
    client = LspClient(FakeTransport(response))
    locs = await client.go_to_implementation("file:///iface.py", 1, 2)
    assert len(locs) == 1
    assert isinstance(locs[0], Location)
    assert locs[0].file == "file:///impl.py"
    assert locs[0].line == 12
    assert locs[0].column == 4


@pytest.mark.asyncio
async def test_go_to_implementation_parses_multiple_locations() -> None:
    response = [
        {"uri": "file:///a.py", "range": {"start": {"line": 1, "character": 0}, "end": {"line": 1, "character": 4}}},
        {"uri": "file:///b.py", "range": {"start": {"line": 2, "character": 0}, "end": {"line": 2, "character": 4}}},
    ]
    client = LspClient(FakeTransport(response))
    locs = await client.go_to_implementation("file:///iface.py", 0, 0)
    assert {l.file for l in locs} == {"file:///a.py", "file:///b.py"}


@pytest.mark.asyncio
async def test_go_to_implementation_returns_empty_on_null() -> None:
    client = LspClient(FakeTransport(None))
    assert await client.go_to_implementation("file:///x.py", 0, 0) == []


@pytest.mark.asyncio
async def test_go_to_implementation_sends_correct_method() -> None:
    transport = FakeTransport([])
    client = LspClient(transport)
    await client.go_to_implementation("file:///x.py", 7, 8)
    sent = transport.sent[-1]
    assert sent["method"] == "textDocument/implementation"
    assert sent["params"]["position"] == {"line": 7, "character": 8}
