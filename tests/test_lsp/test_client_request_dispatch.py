"""LspClient._request must wait for the matching response id, ignoring
interleaved notifications."""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from llm_code.lsp.client import LspClient, LspTransport


class _NoisyTransport(LspTransport):
    """Fake transport that emits two notifications before the response."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def send_message(self, message: dict[str, Any]) -> None:
        self.sent.append(message)
        await self._queue.put(
            {
                "jsonrpc": "2.0",
                "method": "window/logMessage",
                "params": {"type": 3, "message": "noise"},
            }
        )
        await self._queue.put(
            {
                "jsonrpc": "2.0",
                "method": "$/progress",
                "params": {"token": "x", "value": {"kind": "begin", "title": "indexing"}},
            }
        )
        await self._queue.put(
            {
                "jsonrpc": "2.0",
                "id": message["id"],
                "result": {"matched": True},
            }
        )

    async def receive_message(self) -> dict[str, Any]:
        return await self._queue.get()


@pytest.mark.asyncio
async def test_request_skips_interleaved_notifications() -> None:
    transport = _NoisyTransport()
    client = LspClient(transport)
    result = await client._request("custom/method", {"foo": "bar"})
    assert result == {"matched": True}


@pytest.mark.asyncio
async def test_request_matches_id_under_concurrent_calls() -> None:
    """Two concurrent _request calls must each get their own matching response."""
    sent: list[dict[str, Any]] = []
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    class _Concurrent(LspTransport):
        async def start(self) -> None:
            return None

        async def close(self) -> None:
            return None

        async def send_message(self, message: dict[str, Any]) -> None:
            sent.append(message)
            # Schedule the response (reverse order so IDs get mismatched if
            # dispatch is broken).
            delay = 0.02 if message["id"] == 1 else 0.01
            echo = message["params"]["echo"]
            mid = message["id"]
            asyncio.get_event_loop().call_later(
                delay,
                lambda: queue.put_nowait(
                    {"jsonrpc": "2.0", "id": mid, "result": echo}
                ),
            )

        async def receive_message(self) -> dict[str, Any]:
            return await queue.get()

    client = LspClient(_Concurrent())
    a, b = await asyncio.gather(
        client._request("echo", {"echo": "alpha"}),
        client._request("echo", {"echo": "beta"}),
    )
    assert {a, b} == {"alpha", "beta"}
