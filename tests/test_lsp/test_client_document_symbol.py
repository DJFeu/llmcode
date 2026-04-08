"""LspClient.document_symbol() tests."""
from __future__ import annotations

from typing import Any

import pytest

from llm_code.lsp.client import LspClient, LspTransport, SymbolInfo


class FakeTransport(LspTransport):
    def __init__(self, response: Any) -> None:
        self._response = response
        self.sent: list[dict[str, Any]] = []

    async def start(self) -> None: ...
    async def close(self) -> None: ...

    async def send_message(self, message: dict[str, Any]) -> None:
        self.sent.append(message)

    async def receive_message(self) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": self.sent[-1]["id"], "result": self._response}


@pytest.mark.asyncio
async def test_document_symbol_parses_documentsymbol_shape() -> None:
    response = [
        {
            "name": "MyClass",
            "kind": 5,
            "range": {"start": {"line": 10, "character": 0}, "end": {"line": 30, "character": 0}},
            "selectionRange": {"start": {"line": 10, "character": 6}, "end": {"line": 10, "character": 13}},
            "children": [
                {
                    "name": "method_a",
                    "kind": 6,
                    "range": {"start": {"line": 12, "character": 4}, "end": {"line": 15, "character": 0}},
                    "selectionRange": {"start": {"line": 12, "character": 8}, "end": {"line": 12, "character": 16}},
                }
            ],
        }
    ]
    client = LspClient(FakeTransport(response))
    symbols = await client.document_symbol("file:///x.py")
    names = [s.name for s in symbols]
    assert "MyClass" in names
    assert "method_a" in names
    assert all(isinstance(s, SymbolInfo) for s in symbols)


@pytest.mark.asyncio
async def test_document_symbol_parses_symbolinformation_shape() -> None:
    response = [
        {
            "name": "foo",
            "kind": 12,
            "location": {
                "uri": "file:///x.py",
                "range": {"start": {"line": 1, "character": 0}, "end": {"line": 1, "character": 3}},
            },
        }
    ]
    client = LspClient(FakeTransport(response))
    symbols = await client.document_symbol("file:///x.py")
    assert len(symbols) == 1
    assert symbols[0].name == "foo"
    assert symbols[0].kind == "function"


@pytest.mark.asyncio
async def test_document_symbol_returns_empty_on_null() -> None:
    client = LspClient(FakeTransport(None))
    symbols = await client.document_symbol("file:///x.py")
    assert symbols == []
