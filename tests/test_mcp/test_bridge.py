"""Tests for McpToolBridge and McpServerManager (Task 4)."""
from __future__ import annotations

from typing import Any

import pytest

from llm_code.mcp.client import McpClient
from llm_code.mcp.transport import McpTransport
from llm_code.mcp.types import McpToolDefinition
from llm_code.mcp.bridge import McpToolBridge
from llm_code.mcp.manager import McpServerManager
from llm_code.tools.base import PermissionLevel, ToolResult
from llm_code.tools.registry import ToolRegistry


# ---------------------------------------------------------------------------
# Shared fake transport
# ---------------------------------------------------------------------------

class FakeTransport(McpTransport):
    """In-memory transport returning pre-configured responses in order."""

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.sent: list[dict[str, Any]] = []
        self._responses = list(responses)
        self.started = False
        self.closed = False

    async def start(self) -> None:
        self.started = True

    async def send(self, message: dict[str, Any]) -> None:
        self.sent.append(message)

    async def receive(self) -> dict[str, Any]:
        if not self._responses:
            raise RuntimeError("No more fake responses")
        return self._responses.pop(0)

    async def close(self) -> None:
        self.closed = True


def ok(request_id: int, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _make_tool_def(
    name: str = "create_issue",
    description: str = "Create a GitHub issue",
    input_schema: dict | None = None,
    annotations: dict | None = None,
) -> McpToolDefinition:
    return McpToolDefinition(
        name=name,
        description=description,
        input_schema=input_schema or {"type": "object", "properties": {"title": {"type": "string"}}},
        annotations=annotations,
    )


def _make_client_with_responses(responses: list[dict[str, Any]]) -> tuple[McpClient, FakeTransport]:
    transport = FakeTransport(responses)
    client = McpClient(transport)
    return client, transport


# ---------------------------------------------------------------------------
# McpToolBridge — property tests
# ---------------------------------------------------------------------------

class TestMcpToolBridgeProperties:
    def test_name_format(self):
        tool_def = _make_tool_def("create_issue")
        client, _ = _make_client_with_responses([])
        bridge = McpToolBridge("github", tool_def, client)
        assert bridge.name == "mcp__github__create_issue"

    def test_name_format_different_server(self):
        tool_def = _make_tool_def("list_files")
        client, _ = _make_client_with_responses([])
        bridge = McpToolBridge("filesystem", tool_def, client)
        assert bridge.name == "mcp__filesystem__list_files"

    def test_description_from_mcp_tool(self):
        tool_def = _make_tool_def(description="Creates a new GitHub issue")
        client, _ = _make_client_with_responses([])
        bridge = McpToolBridge("github", tool_def, client)
        assert bridge.description == "Creates a new GitHub issue"

    def test_input_schema_from_mcp_tool(self):
        schema = {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]}
        tool_def = _make_tool_def(input_schema=schema)
        client, _ = _make_client_with_responses([])
        bridge = McpToolBridge("github", tool_def, client)
        assert bridge.input_schema == schema

    def test_required_permission_is_full_access(self):
        tool_def = _make_tool_def()
        client, _ = _make_client_with_responses([])
        bridge = McpToolBridge("github", tool_def, client)
        assert bridge.required_permission == PermissionLevel.FULL_ACCESS


# ---------------------------------------------------------------------------
# McpToolBridge — annotation-derived methods
# ---------------------------------------------------------------------------

class TestMcpToolBridgeAnnotations:
    def test_is_read_only_true_when_annotation_set(self):
        tool_def = _make_tool_def(annotations={"readOnly": True})
        client, _ = _make_client_with_responses([])
        bridge = McpToolBridge("github", tool_def, client)
        assert bridge.is_read_only({}) is True

    def test_is_read_only_false_when_annotation_false(self):
        tool_def = _make_tool_def(annotations={"readOnly": False})
        client, _ = _make_client_with_responses([])
        bridge = McpToolBridge("github", tool_def, client)
        assert bridge.is_read_only({}) is False

    def test_is_read_only_false_when_no_annotations(self):
        tool_def = _make_tool_def(annotations=None)
        client, _ = _make_client_with_responses([])
        bridge = McpToolBridge("github", tool_def, client)
        assert bridge.is_read_only({}) is False

    def test_is_read_only_false_when_annotation_missing_key(self):
        tool_def = _make_tool_def(annotations={"destructive": True})
        client, _ = _make_client_with_responses([])
        bridge = McpToolBridge("github", tool_def, client)
        assert bridge.is_read_only({}) is False

    def test_is_destructive_true_when_annotation_set(self):
        tool_def = _make_tool_def(annotations={"destructive": True})
        client, _ = _make_client_with_responses([])
        bridge = McpToolBridge("github", tool_def, client)
        assert bridge.is_destructive({}) is True

    def test_is_destructive_false_when_no_annotations(self):
        tool_def = _make_tool_def(annotations=None)
        client, _ = _make_client_with_responses([])
        bridge = McpToolBridge("github", tool_def, client)
        assert bridge.is_destructive({}) is False

    def test_is_concurrency_safe_matches_read_only(self):
        read_only_tool = _make_tool_def(annotations={"readOnly": True})
        write_tool = _make_tool_def(annotations={"readOnly": False})
        client, _ = _make_client_with_responses([])
        read_bridge = McpToolBridge("github", read_only_tool, client)
        write_bridge = McpToolBridge("github", write_tool, client)
        assert read_bridge.is_concurrency_safe({}) is True
        assert write_bridge.is_concurrency_safe({}) is False


# ---------------------------------------------------------------------------
# McpToolBridge — execute
# ---------------------------------------------------------------------------

class TestMcpToolBridgeExecute:
    def test_execute_returns_tool_result_on_success(self):
        tool_def = _make_tool_def("create_issue")
        client, _ = _make_client_with_responses([
            ok(1, {
                "content": [{"type": "text", "text": "Issue #42 created"}],
                "isError": False,
            })
        ])
        bridge = McpToolBridge("github", tool_def, client)
        result = bridge.execute({"title": "Bug report"})
        assert isinstance(result, ToolResult)
        assert result.output == "Issue #42 created"
        assert result.is_error is False

    def test_execute_returns_error_result_on_mcp_error(self):
        tool_def = _make_tool_def("create_issue")
        client, _ = _make_client_with_responses([
            ok(1, {
                "content": [{"type": "text", "text": "Unauthorized"}],
                "isError": True,
            })
        ])
        bridge = McpToolBridge("github", tool_def, client)
        result = bridge.execute({"title": "Test"})
        assert result.is_error is True
        assert result.output == "Unauthorized"

    def test_execute_passes_args_to_client(self):
        tool_def = _make_tool_def("create_issue")
        client, transport = _make_client_with_responses([
            ok(1, {
                "content": [{"type": "text", "text": "done"}],
                "isError": False,
            })
        ])
        bridge = McpToolBridge("github", tool_def, client)
        bridge.execute({"title": "My Issue", "body": "Details here"})

        assert len(transport.sent) == 1
        msg = transport.sent[0]
        assert msg["method"] == "tools/call"
        assert msg["params"]["name"] == "create_issue"
        assert msg["params"]["arguments"] == {"title": "My Issue", "body": "Details here"}

    def test_execute_propagates_exception_as_error_result(self):
        """When client.call_tool raises (e.g. transport error), execute should surface it."""
        tool_def = _make_tool_def("create_issue")
        # No responses → receive() raises RuntimeError
        client, _ = _make_client_with_responses([
            ok(1, {"content": [{"type": "text", "text": "boom"}], "isError": True})
        ])
        # Inject a bad response to trigger error propagation
        bad_client, _ = _make_client_with_responses([])  # empty → raises
        bridge = McpToolBridge("github", tool_def, bad_client)
        result = bridge.execute({"title": "X"})
        assert result.is_error is True
        assert len(result.output) > 0


# ---------------------------------------------------------------------------
# McpToolBridge — to_definition
# ---------------------------------------------------------------------------

class TestMcpToolBridgeToDefinition:
    def test_to_definition_returns_correct_name_and_description(self):
        schema = {"type": "object"}
        tool_def = _make_tool_def("search_code", "Search code in a repo", schema)
        client, _ = _make_client_with_responses([])
        bridge = McpToolBridge("github", tool_def, client)
        defn = bridge.to_definition()
        assert defn.name == "mcp__github__search_code"
        assert defn.description == "Search code in a repo"
        assert defn.input_schema == schema


# ---------------------------------------------------------------------------
# McpServerManager
# ---------------------------------------------------------------------------

class TestMcpServerManagerRegisterAllTools:
    @pytest.mark.asyncio
    async def test_register_all_tools_registers_tools_in_registry(self):
        """register_all_tools should discover MCP tools and register bridges in ToolRegistry."""
        # Pre-populated client: only list_tools is called (id=1 from fresh counter)
        list_tools_response = ok(1, {
            "tools": [
                {
                    "name": "create_issue",
                    "description": "Create a GitHub issue",
                    "inputSchema": {"type": "object"},
                },
                {
                    "name": "list_repos",
                    "description": "List repositories",
                    "inputSchema": {"type": "object"},
                    "annotations": {"readOnly": True},
                },
            ]
        })
        transport = FakeTransport([list_tools_response])
        client = McpClient(transport)

        # Manually pre-populate manager with this client (bypasses transport creation)
        manager = McpServerManager()
        manager._clients["github"] = client  # type: ignore[attr-defined]

        registry = ToolRegistry()
        count = await manager.register_all_tools(registry)

        assert count == 2
        assert registry.get("mcp__github__create_issue") is not None
        assert registry.get("mcp__github__list_repos") is not None

    @pytest.mark.asyncio
    async def test_register_all_tools_returns_zero_with_no_clients(self):
        manager = McpServerManager()
        registry = ToolRegistry()
        count = await manager.register_all_tools(registry)
        assert count == 0

    @pytest.mark.asyncio
    async def test_registered_tool_is_read_only_bridge(self):
        # Pre-populated client: only list_tools is called (id=1 from fresh counter)
        list_tools_response = ok(1, {
            "tools": [
                {
                    "name": "read_file",
                    "description": "Read a file",
                    "inputSchema": {"type": "object"},
                    "annotations": {"readOnly": True},
                }
            ]
        })
        transport = FakeTransport([list_tools_response])
        client = McpClient(transport)

        manager = McpServerManager()
        manager._clients["filesystem"] = client  # type: ignore[attr-defined]

        registry = ToolRegistry()
        await manager.register_all_tools(registry)

        tool = registry.get("mcp__filesystem__read_file")
        assert tool is not None
        assert tool.is_read_only({}) is True
        assert isinstance(tool, McpToolBridge)


class TestMcpServerManagerLifecycle:
    @pytest.mark.asyncio
    async def test_get_client_returns_none_for_unknown_server(self):
        manager = McpServerManager()
        assert manager.get_client("nonexistent") is None

    @pytest.mark.asyncio
    async def test_stop_all_clears_clients(self):
        """stop_all should close clients and clear internal state."""
        close_transport = FakeTransport([])
        client = McpClient(close_transport)

        manager = McpServerManager()
        manager._clients["myserver"] = client  # type: ignore[attr-defined]
        manager._transports["myserver"] = close_transport  # type: ignore[attr-defined]

        await manager.stop_all()

        assert manager.get_client("myserver") is None
        assert close_transport.closed is True

    @pytest.mark.asyncio
    async def test_get_client_returns_registered_client(self):
        transport = FakeTransport([])
        client = McpClient(transport)

        manager = McpServerManager()
        manager._clients["myserver"] = client  # type: ignore[attr-defined]

        assert manager.get_client("myserver") is client
