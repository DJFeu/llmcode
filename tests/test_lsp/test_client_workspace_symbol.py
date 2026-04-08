"""LspClient.workspace_symbol() tests."""
from __future__ import annotations

from typing import Any

import pytest

from llm_code.lsp.client import LspClient, LspTransport, SymbolInfo  # noqa: F401


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
async def test_workspace_symbol_returns_symbol_information_list() -> None:
    response = [
        {
            "name": "MyHelper",
            "kind": 5,
            "location": {
                "uri": "file:///x.py",
                "range": {"start": {"line": 10, "character": 0}, "end": {"line": 10, "character": 8}},
            },
        }
    ]
    client = LspClient(FakeTransport(response))
    out = await client.workspace_symbol("MyHelper")
    assert len(out) == 1
    assert out[0].name == "MyHelper"
    assert out[0].kind == "class"
    assert out[0].file == "file:///x.py"


@pytest.mark.asyncio
async def test_workspace_symbol_sends_query_param() -> None:
    transport = FakeTransport([])
    client = LspClient(transport)
    await client.workspace_symbol("foo")
    sent = transport.sent[-1]
    assert sent["method"] == "workspace/symbol"
    assert sent["params"] == {"query": "foo"}


@pytest.mark.asyncio
async def test_workspace_symbol_empty_result() -> None:
    client = LspClient(FakeTransport(None))
    assert await client.workspace_symbol("nope") == []
