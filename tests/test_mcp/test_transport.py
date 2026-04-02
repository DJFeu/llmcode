"""Tests for MCP transport layer (Task 2 + Feature 6)."""
import pytest

from llm_code.mcp.transport import HttpTransport, McpTransport, SseTransport, StdioTransport, WebSocketTransport


class TestMcpTransportABC:
    def test_cannot_instantiate_abc(self):
        with pytest.raises(TypeError):
            McpTransport()  # type: ignore


class TestStdioTransport:
    @pytest.mark.asyncio
    async def test_echo_roundtrip(self):
        """Use a Python echo script as mock server."""
        transport = StdioTransport(
            command="python3",
            args=(
                "-c",
                (
                    "import sys, json\n"
                    "for line in iter(sys.stdin.readline, ''):\n"
                    "    sys.stdout.write(json.dumps({**json.loads(line), 'echoed': True}) + '\\n')\n"
                    "    sys.stdout.flush()\n"
                ),
            ),
        )
        await transport.start()
        try:
            await transport.send({"id": 1, "method": "ping"})
            response = await transport.receive()
            assert response["id"] == 1
            assert response["method"] == "ping"
            assert response["echoed"] is True
        finally:
            await transport.close()

    @pytest.mark.asyncio
    async def test_close_is_safe_to_call_twice(self):
        """Double close should not raise."""
        transport = StdioTransport(
            command="python3",
            args=("-c", "import sys; sys.stdin.read()"),
        )
        await transport.start()
        await transport.close()
        await transport.close()  # should not raise

    @pytest.mark.asyncio
    async def test_close_before_start_is_safe(self):
        """Close without start should not raise."""
        transport = StdioTransport(command="python3", args=("-c", "pass"))
        await transport.close()  # should not raise

    def test_init_stores_command_and_args(self):
        transport = StdioTransport(command="python3", args=("script.py",))
        assert transport.command == "python3"
        assert transport.args == ("script.py",)

    def test_init_with_env(self):
        transport = StdioTransport(command="python3", args=(), env={"KEY": "value"})
        assert transport.env == {"KEY": "value"}

    def test_init_env_defaults_to_none(self):
        transport = StdioTransport(command="python3")
        assert transport.env is None


class TestHttpTransport:
    def test_init_stores_url(self):
        transport = HttpTransport(url="http://localhost:8080/mcp")
        assert transport.url == "http://localhost:8080/mcp"

    def test_init_stores_headers(self):
        transport = HttpTransport(
            url="http://localhost:8080/mcp",
            headers={"Authorization": "Bearer token"},
        )
        assert transport.headers == {"Authorization": "Bearer token"}

    def test_init_headers_defaults_to_none(self):
        transport = HttpTransport(url="http://localhost:8080/mcp")
        assert transport.headers is None

    @pytest.mark.asyncio
    async def test_start_creates_client(self):
        transport = HttpTransport(url="http://localhost:8080/mcp")
        await transport.start()
        assert transport._client is not None
        await transport.close()

    @pytest.mark.asyncio
    async def test_close_is_safe(self):
        transport = HttpTransport(url="http://localhost:8080/mcp")
        await transport.start()
        await transport.close()
        await transport.close()  # should not raise


class TestSseTransport:
    def test_init_stores_url(self):
        transport = SseTransport(url="http://localhost:8080/sse")
        assert transport._url == "http://localhost:8080/sse"

    def test_init_stores_headers(self):
        transport = SseTransport(
            url="http://localhost:8080/sse",
            headers={"Authorization": "Bearer token"},
        )
        assert transport._headers == {"Authorization": "Bearer token"}

    def test_init_headers_defaults_to_empty_dict(self):
        transport = SseTransport(url="http://localhost:8080/sse")
        assert transport._headers == {}

    def test_init_client_is_none_before_start(self):
        transport = SseTransport(url="http://localhost:8080/sse")
        assert transport._client is None

    @pytest.mark.asyncio
    async def test_start_creates_client(self):
        transport = SseTransport(url="http://localhost:8080/sse")
        await transport.start()
        assert transport._client is not None
        await transport.close()

    @pytest.mark.asyncio
    async def test_close_is_safe_before_start(self):
        transport = SseTransport(url="http://localhost:8080/sse")
        await transport.close()  # should not raise

    @pytest.mark.asyncio
    async def test_close_is_safe_after_start(self):
        transport = SseTransport(url="http://localhost:8080/sse")
        await transport.start()
        await transport.close()
        await transport.close()  # double-close should not raise

    @pytest.mark.asyncio
    async def test_send_raises_if_not_started(self):
        transport = SseTransport(url="http://localhost:8080/sse")
        with pytest.raises(RuntimeError, match="not started"):
            await transport.send({"method": "ping"})

    @pytest.mark.asyncio
    async def test_start_includes_sse_accept_header(self):
        transport = SseTransport(url="http://localhost:8080/sse")
        await transport.start()
        assert transport._client is not None
        # The Accept header should be set on the client
        assert transport._client.headers.get("accept") == "text/event-stream"
        await transport.close()

    @pytest.mark.asyncio
    async def test_receive_returns_queued_item(self):
        transport = SseTransport(url="http://localhost:8080/sse")
        # Directly queue a fake response and verify receive returns it
        await transport._response_queue.put({"result": "ok"})
        result = await transport.receive()
        assert result == {"result": "ok"}


class TestWebSocketTransport:
    def test_init_stores_url(self):
        transport = WebSocketTransport(url="ws://localhost:8080/ws")
        assert transport._url == "ws://localhost:8080/ws"

    def test_init_stores_headers(self):
        transport = WebSocketTransport(
            url="ws://localhost:8080/ws",
            headers={"Authorization": "Bearer token"},
        )
        assert transport._headers == {"Authorization": "Bearer token"}

    def test_init_headers_defaults_to_empty_dict(self):
        transport = WebSocketTransport(url="ws://localhost:8080/ws")
        assert transport._headers == {}

    def test_init_ws_is_none_before_start(self):
        transport = WebSocketTransport(url="ws://localhost:8080/ws")
        assert transport._ws is None

    @pytest.mark.asyncio
    async def test_start_raises_import_error_without_websockets(self, monkeypatch):
        """When websockets is not installed, start() should raise ImportError."""
        import sys
        # Temporarily hide websockets from import machinery
        original = sys.modules.get("websockets")
        sys.modules["websockets"] = None  # type: ignore[assignment]
        try:
            transport = WebSocketTransport(url="ws://localhost:8080/ws")
            with pytest.raises(ImportError, match="websockets"):
                await transport.start()
        finally:
            if original is not None:
                sys.modules["websockets"] = original
            else:
                sys.modules.pop("websockets", None)

    @pytest.mark.asyncio
    async def test_send_raises_if_not_connected(self):
        transport = WebSocketTransport(url="ws://localhost:8080/ws")
        with pytest.raises(RuntimeError, match="not connected"):
            await transport.send({"method": "ping"})

    @pytest.mark.asyncio
    async def test_receive_raises_if_not_connected(self):
        transport = WebSocketTransport(url="ws://localhost:8080/ws")
        with pytest.raises(RuntimeError, match="not connected"):
            await transport.receive()

    @pytest.mark.asyncio
    async def test_close_is_safe_before_start(self):
        transport = WebSocketTransport(url="ws://localhost:8080/ws")
        await transport.close()  # should not raise

    @pytest.mark.asyncio
    async def test_close_sets_ws_to_none(self):
        """After close, _ws should be None even if it was set."""
        from unittest.mock import AsyncMock

        transport = WebSocketTransport(url="ws://localhost:8080/ws")
        mock_ws = AsyncMock()
        transport._ws = mock_ws
        await transport.close()
        assert transport._ws is None
        mock_ws.close.assert_called_once()
