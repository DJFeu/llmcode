"""MCP transport layer: ABC, StdioTransport, HttpTransport, SseTransport, WebSocketTransport."""
from __future__ import annotations

import asyncio
import json
import os
from abc import ABC, abstractmethod
from typing import Any

import httpx


class McpTransport(ABC):
    """Abstract base class for MCP transports."""

    @abstractmethod
    async def start(self) -> None:
        """Start the transport (connect or launch subprocess)."""

    @abstractmethod
    async def send(self, message: dict[str, Any]) -> None:
        """Send a JSON message."""

    @abstractmethod
    async def receive(self) -> dict[str, Any]:
        """Receive and return the next JSON message."""

    @abstractmethod
    async def close(self) -> None:
        """Close and clean up the transport."""


class StdioTransport(McpTransport):
    """MCP transport that communicates via subprocess stdin/stdout."""

    RECEIVE_TIMEOUT = 30.0
    CLOSE_WAIT_TIMEOUT = 5.0

    def __init__(
        self,
        command: str,
        args: tuple[str, ...] = (),
        env: dict[str, str] | None = None,
    ) -> None:
        self.command = command
        self.args = args
        self.env = env
        self._process: asyncio.subprocess.Process | None = None

    async def start(self) -> None:
        """Launch the subprocess with merged environment."""
        merged_env = {**os.environ}
        if self.env:
            merged_env.update(self.env)

        self._process = await asyncio.create_subprocess_exec(
            self.command,
            *self.args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=merged_env,
        )

    async def send(self, message: dict[str, Any]) -> None:
        """Write JSON + newline to subprocess stdin."""
        if self._process is None or self._process.stdin is None:
            raise RuntimeError("Transport not started")
        data = json.dumps(message) + "\n"
        self._process.stdin.write(data.encode())
        await self._process.stdin.drain()

    async def receive(self) -> dict[str, Any]:
        """Read a line from subprocess stdout with timeout, parse JSON."""
        if self._process is None or self._process.stdout is None:
            raise RuntimeError("Transport not started")
        line = await asyncio.wait_for(
            self._process.stdout.readline(),
            timeout=self.RECEIVE_TIMEOUT,
        )
        return json.loads(line.decode().strip())

    async def close(self) -> None:
        """Terminate subprocess gracefully, kill if needed."""
        if self._process is None:
            return
        process = self._process
        self._process = None

        try:
            if process.stdin and not process.stdin.is_closing():
                process.stdin.close()
        except Exception:
            pass

        try:
            process.terminate()
        except (ProcessLookupError, OSError):
            pass

        try:
            await asyncio.wait_for(process.wait(), timeout=self.CLOSE_WAIT_TIMEOUT)
        except asyncio.TimeoutError:
            try:
                process.kill()
            except (ProcessLookupError, OSError):
                pass
            try:
                await process.wait()
            except Exception:
                pass


class HttpTransport(McpTransport):
    """MCP transport that communicates via HTTP POST requests."""

    def __init__(
        self,
        url: str,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.url = url
        self.headers = headers
        self._client: httpx.AsyncClient | None = None
        self._last_response: dict[str, Any] | None = None

    async def start(self) -> None:
        """Create the HTTP client."""
        self._client = httpx.AsyncClient(headers=self.headers or {})

    async def send(self, message: dict[str, Any]) -> None:
        """POST the JSON message to the server URL and store the response."""
        if self._client is None:
            raise RuntimeError("Transport not started")
        response = await self._client.post(self.url, json=message)
        response.raise_for_status()
        self._last_response = response.json()

    async def receive(self) -> dict[str, Any]:
        """Return the stored response from the last send()."""
        if self._last_response is None:
            raise RuntimeError("No response available; call send() first")
        result = self._last_response
        self._last_response = None
        return result

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client is None:
            return
        client = self._client
        self._client = None
        await client.aclose()


class SseTransport(McpTransport):
    """SSE (Server-Sent Events) transport for MCP.

    Server-Sent Events are server→client by nature; outbound messages are
    sent via HTTP POST to the same URL, and responses are queued for
    retrieval via ``receive()``.
    """

    def __init__(
        self,
        url: str,
        headers: dict[str, str] | None = None,
    ) -> None:
        self._url = url
        self._headers: dict[str, str] = headers or {}
        self._client: httpx.AsyncClient | None = None
        self._response_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    async def start(self) -> None:
        """Create the HTTP client with SSE accept header."""
        self._client = httpx.AsyncClient(
            headers={"Accept": "text/event-stream", **self._headers},
            timeout=httpx.Timeout(30.0),
        )

    async def send(self, message: dict[str, Any]) -> None:
        """Send a message via HTTP POST and enqueue the response."""
        if self._client is None:
            raise RuntimeError("Transport not started")
        resp = await self._client.post(self._url, json=message)
        resp.raise_for_status()
        await self._response_queue.put(resp.json())

    async def receive(self) -> dict[str, Any]:
        """Return the next queued response."""
        return await self._response_queue.get()

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client is None:
            return
        client = self._client
        self._client = None
        await client.aclose()


class WebSocketTransport(McpTransport):
    """WebSocket transport for MCP.

    Requires the optional ``websockets`` package::

        pip install websockets
        # or: pip install llm-code[websocket]
    """

    def __init__(
        self,
        url: str,
        headers: dict[str, str] | None = None,
    ) -> None:
        self._url = url
        self._headers: dict[str, str] = headers or {}
        self._ws: Any = None  # websockets.WebSocketClientProtocol

    async def start(self) -> None:
        """Connect to the WebSocket server."""
        try:
            import websockets  # type: ignore[import-untyped]
        except ImportError as exc:
            raise ImportError(
                "WebSocket transport requires the 'websockets' package: "
                "pip install websockets"
            ) from exc

        self._ws = await websockets.connect(
            self._url,
            additional_headers=self._headers,
        )

    async def send(self, message: dict[str, Any]) -> None:
        """Send a JSON message over the WebSocket."""
        if self._ws is None:
            raise RuntimeError("WebSocket not connected")
        await self._ws.send(json.dumps(message))

    async def receive(self) -> dict[str, Any]:
        """Receive and parse the next JSON message from the WebSocket."""
        if self._ws is None:
            raise RuntimeError("WebSocket not connected")
        data = await self._ws.recv()
        return json.loads(data)  # type: ignore[no-any-return]

    async def close(self) -> None:
        """Close the WebSocket connection."""
        if self._ws is None:
            return
        ws = self._ws
        self._ws = None
        await ws.close()
