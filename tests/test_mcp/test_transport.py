"""Tests for MCP transport layer (Task 2)."""
import pytest

from llm_code.mcp.transport import McpTransport, StdioTransport, HttpTransport


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
