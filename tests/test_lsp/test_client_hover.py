"""LspClient.hover() unit tests using a fake transport."""
from __future__ import annotations

from typing import Any

import pytest

from llm_code.lsp.client import Hover, LspClient, LspTransport


class FakeTransport(LspTransport):
    def __init__(self, response: dict[str, Any]) -> None:
        self._response = response
        self.sent: list[dict[str, Any]] = []

    async def start(self) -> None: ...
    async def close(self) -> None: ...

    async def send_message(self, message: dict[str, Any]) -> None:
        self.sent.append(message)

    async def receive_message(self) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": self.sent[-1]["id"], "result": self._response}


@pytest.mark.asyncio
async def test_hover_parses_markup_content_string() -> None:
    transport = FakeTransport(
        {"contents": {"kind": "markdown", "value": "**foo**: int"}}
    )
    client = LspClient(transport)
    hover = await client.hover("file:///x.py", 1, 2)
    assert isinstance(hover, Hover)
    assert hover.contents == "**foo**: int"


@pytest.mark.asyncio
async def test_hover_parses_legacy_marked_string_list() -> None:
    transport = FakeTransport({"contents": ["foo: int", "bar"]})
    client = LspClient(transport)
    hover = await client.hover("file:///x.py", 1, 2)
    assert "foo: int" in hover.contents
    assert "bar" in hover.contents


@pytest.mark.asyncio
async def test_hover_parses_marked_string_object() -> None:
    transport = FakeTransport(
        {"contents": [{"language": "python", "value": "foo: int"}]}
    )
    client = LspClient(transport)
    hover = await client.hover("file:///x.py", 1, 2)
    assert "foo: int" in hover.contents


@pytest.mark.asyncio
async def test_hover_returns_empty_on_null_result() -> None:
    transport = FakeTransport({})
    transport._response = None  # type: ignore[assignment]
    client = LspClient(transport)
    hover = await client.hover("file:///x.py", 0, 0)
    assert hover.contents == ""


@pytest.mark.asyncio
async def test_hover_sends_correct_jsonrpc_payload() -> None:
    transport = FakeTransport({"contents": "ok"})
    client = LspClient(transport)
    await client.hover("file:///x.py", 5, 7)
    sent = transport.sent[-1]
    assert sent["method"] == "textDocument/hover"
    assert sent["params"]["textDocument"]["uri"] == "file:///x.py"
    assert sent["params"]["position"] == {"line": 5, "character": 7}
