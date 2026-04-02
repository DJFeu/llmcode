"""Tests for MCP client (Task 3) — uses MockTransport."""
from __future__ import annotations

import pytest
from typing import Any

from llm_code.mcp.client import McpClient
from llm_code.mcp.transport import McpTransport
from llm_code.mcp.types import McpServerInfo, McpToolDefinition, McpToolResult, McpResource


class MockTransport(McpTransport):
    """In-memory transport for testing: stores sent messages, returns preset responses."""

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.sent: list[dict[str, Any]] = []
        self._responses = list(responses)
        self.closed = False

    async def start(self) -> None:
        pass

    async def send(self, message: dict[str, Any]) -> None:
        self.sent.append(message)

    async def receive(self) -> dict[str, Any]:
        if not self._responses:
            raise RuntimeError("No more mock responses")
        return self._responses.pop(0)

    async def close(self) -> None:
        self.closed = True


def make_success_response(request_id: int, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def make_error_response(request_id: int, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


class TestMcpClientInitialize:
    @pytest.mark.asyncio
    async def test_initialize_parses_server_info(self):
        transport = MockTransport([
            make_success_response(1, {
                "serverInfo": {"name": "test-server", "version": "1.2.3"},
                "capabilities": {"tools": {}, "resources": {}},
            })
        ])
        client = McpClient(transport)
        info = await client.initialize()

        assert isinstance(info, McpServerInfo)
        assert info.name == "test-server"
        assert info.version == "1.2.3"
        assert info.capabilities == {"tools": {}, "resources": {}}

    @pytest.mark.asyncio
    async def test_initialize_sends_correct_method(self):
        transport = MockTransport([
            make_success_response(1, {
                "serverInfo": {"name": "s", "version": "1"},
                "capabilities": {},
            })
        ])
        client = McpClient(transport)
        await client.initialize()

        assert len(transport.sent) == 1
        msg = transport.sent[0]
        assert msg["jsonrpc"] == "2.0"
        assert msg["method"] == "initialize"
        assert "params" in msg
        assert "protocolVersion" in msg["params"]
        assert "clientInfo" in msg["params"]

    @pytest.mark.asyncio
    async def test_initialize_id_increments(self):
        transport = MockTransport([
            make_success_response(1, {
                "serverInfo": {"name": "s", "version": "1"},
                "capabilities": {},
            }),
            make_success_response(2, {"tools": []}),
        ])
        client = McpClient(transport)
        await client.initialize()
        await client.list_tools()

        assert transport.sent[0]["id"] == 1
        assert transport.sent[1]["id"] == 2


class TestMcpClientListTools:
    @pytest.mark.asyncio
    async def test_list_tools_returns_tool_definitions(self):
        transport = MockTransport([
            make_success_response(1, {
                "tools": [
                    {
                        "name": "run_shell",
                        "description": "Run a shell command",
                        "inputSchema": {"type": "object"},
                    },
                    {
                        "name": "read_file",
                        "description": "Read a file",
                        "inputSchema": {"type": "object"},
                        "annotations": {"readOnly": True},
                    },
                ]
            })
        ])
        client = McpClient(transport)
        tools = await client.list_tools()

        assert len(tools) == 2
        assert all(isinstance(t, McpToolDefinition) for t in tools)
        assert tools[0].name == "run_shell"
        assert tools[0].description == "Run a shell command"
        assert tools[0].input_schema == {"type": "object"}
        assert tools[0].annotations is None
        assert tools[1].annotations == {"readOnly": True}

    @pytest.mark.asyncio
    async def test_list_tools_empty(self):
        transport = MockTransport([make_success_response(1, {"tools": []})])
        client = McpClient(transport)
        tools = await client.list_tools()
        assert tools == []

    @pytest.mark.asyncio
    async def test_list_tools_sends_correct_method(self):
        transport = MockTransport([make_success_response(1, {"tools": []})])
        client = McpClient(transport)
        await client.list_tools()
        assert transport.sent[0]["method"] == "tools/list"


class TestMcpClientCallTool:
    @pytest.mark.asyncio
    async def test_call_tool_extracts_text_content(self):
        transport = MockTransport([
            make_success_response(1, {
                "content": [{"type": "text", "text": "command output"}],
                "isError": False,
            })
        ])
        client = McpClient(transport)
        result = await client.call_tool("run_shell", {"cmd": "ls"})

        assert isinstance(result, McpToolResult)
        assert result.content == "command output"
        assert result.is_error is False

    @pytest.mark.asyncio
    async def test_call_tool_error_result(self):
        transport = MockTransport([
            make_success_response(1, {
                "content": [{"type": "text", "text": "permission denied"}],
                "isError": True,
            })
        ])
        client = McpClient(transport)
        result = await client.call_tool("run_shell", {"cmd": "rm -rf /"})

        assert result.is_error is True
        assert result.content == "permission denied"

    @pytest.mark.asyncio
    async def test_call_tool_sends_correct_params(self):
        transport = MockTransport([
            make_success_response(1, {
                "content": [{"type": "text", "text": "ok"}],
                "isError": False,
            })
        ])
        client = McpClient(transport)
        await client.call_tool("my_tool", {"key": "value"})

        msg = transport.sent[0]
        assert msg["method"] == "tools/call"
        assert msg["params"]["name"] == "my_tool"
        assert msg["params"]["arguments"] == {"key": "value"}


class TestMcpClientListResources:
    @pytest.mark.asyncio
    async def test_list_resources_returns_resource_list(self):
        transport = MockTransport([
            make_success_response(1, {
                "resources": [
                    {
                        "uri": "file:///path/to/file.txt",
                        "name": "file.txt",
                        "description": "A text file",
                        "mimeType": "text/plain",
                    }
                ]
            })
        ])
        client = McpClient(transport)
        resources = await client.list_resources()

        assert len(resources) == 1
        assert isinstance(resources[0], McpResource)
        assert resources[0].uri == "file:///path/to/file.txt"
        assert resources[0].name == "file.txt"
        assert resources[0].description == "A text file"
        assert resources[0].mime_type == "text/plain"

    @pytest.mark.asyncio
    async def test_list_resources_sends_correct_method(self):
        transport = MockTransport([make_success_response(1, {"resources": []})])
        client = McpClient(transport)
        await client.list_resources()
        assert transport.sent[0]["method"] == "resources/list"


class TestMcpClientReadResource:
    @pytest.mark.asyncio
    async def test_read_resource_returns_text(self):
        transport = MockTransport([
            make_success_response(1, {
                "contents": [{"type": "text", "text": "file contents here"}]
            })
        ])
        client = McpClient(transport)
        text = await client.read_resource("file:///path/to/file.txt")
        assert text == "file contents here"

    @pytest.mark.asyncio
    async def test_read_resource_sends_correct_params(self):
        transport = MockTransport([
            make_success_response(1, {"contents": [{"type": "text", "text": ""}]})
        ])
        client = McpClient(transport)
        await client.read_resource("file:///test.py")
        msg = transport.sent[0]
        assert msg["method"] == "resources/read"
        assert msg["params"]["uri"] == "file:///test.py"


class TestMcpClientErrorHandling:
    @pytest.mark.asyncio
    async def test_json_rpc_error_raises_runtime_error(self):
        transport = MockTransport([
            make_error_response(1, -32601, "Method not found")
        ])
        client = McpClient(transport)
        with pytest.raises(RuntimeError, match="Method not found"):
            await client.list_tools()

    @pytest.mark.asyncio
    async def test_json_rpc_error_message_included(self):
        transport = MockTransport([
            make_error_response(1, -32600, "Invalid Request")
        ])
        client = McpClient(transport)
        with pytest.raises(RuntimeError) as exc_info:
            await client._request("bad/method", {})
        assert "Invalid Request" in str(exc_info.value)


class TestMcpClientClose:
    @pytest.mark.asyncio
    async def test_close_does_not_raise(self):
        transport = MockTransport([])
        client = McpClient(transport)
        await client.close()  # should not raise
        assert transport.closed is True

    @pytest.mark.asyncio
    async def test_close_delegates_to_transport(self):
        transport = MockTransport([])
        client = McpClient(transport)
        assert transport.closed is False
        await client.close()
        assert transport.closed is True
