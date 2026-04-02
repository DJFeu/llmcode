"""Tests for MCP protocol types (Task 1)."""
import dataclasses
import pytest

from llm_code.mcp.types import (
    McpServerConfig,
    McpToolDefinition,
    McpToolResult,
    McpResource,
    McpServerInfo,
)


class TestMcpServerConfig:
    def test_creation_with_defaults(self):
        cfg = McpServerConfig()
        assert cfg.command is None
        assert cfg.args == ()
        assert cfg.env is None
        assert cfg.transport_type == "stdio"
        assert cfg.url is None
        assert cfg.headers is None

    def test_creation_with_values(self):
        cfg = McpServerConfig(
            command="python",
            args=("server.py", "--port", "8080"),
            env={"KEY": "value"},
            transport_type="http",
            url="http://localhost:8080",
            headers={"Authorization": "Bearer token"},
        )
        assert cfg.command == "python"
        assert cfg.args == ("server.py", "--port", "8080")
        assert cfg.env == {"KEY": "value"}
        assert cfg.transport_type == "http"
        assert cfg.url == "http://localhost:8080"
        assert cfg.headers == {"Authorization": "Bearer token"}

    def test_frozen(self):
        cfg = McpServerConfig(command="python")
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            cfg.command = "other"  # type: ignore

    def test_is_dataclass(self):
        assert dataclasses.is_dataclass(McpServerConfig)


class TestMcpToolDefinition:
    def test_creation(self):
        tool = McpToolDefinition(
            name="run_command",
            description="Runs a shell command",
            input_schema={"type": "object", "properties": {"cmd": {"type": "string"}}},
        )
        assert tool.name == "run_command"
        assert tool.description == "Runs a shell command"
        assert tool.input_schema == {"type": "object", "properties": {"cmd": {"type": "string"}}}
        assert tool.annotations is None

    def test_with_annotations(self):
        tool = McpToolDefinition(
            name="tool",
            description="desc",
            input_schema={},
            annotations={"readOnly": True},
        )
        assert tool.annotations == {"readOnly": True}

    def test_frozen(self):
        tool = McpToolDefinition(name="t", description="d", input_schema={})
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            tool.name = "other"  # type: ignore


class TestMcpToolResult:
    def test_creation_with_defaults(self):
        result = McpToolResult(content="output text")
        assert result.content == "output text"
        assert result.is_error is False

    def test_error_result(self):
        result = McpToolResult(content="something went wrong", is_error=True)
        assert result.is_error is True

    def test_frozen(self):
        result = McpToolResult(content="text")
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            result.content = "other"  # type: ignore


class TestMcpResource:
    def test_creation_with_required(self):
        resource = McpResource(uri="file:///path/to/file", name="my_file")
        assert resource.uri == "file:///path/to/file"
        assert resource.name == "my_file"
        assert resource.description is None
        assert resource.mime_type is None

    def test_creation_with_all_fields(self):
        resource = McpResource(
            uri="file:///path/to/file",
            name="my_file",
            description="A test file",
            mime_type="text/plain",
        )
        assert resource.description == "A test file"
        assert resource.mime_type == "text/plain"

    def test_frozen(self):
        resource = McpResource(uri="file:///x", name="x")
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            resource.uri = "other"  # type: ignore


class TestMcpServerInfo:
    def test_creation(self):
        info = McpServerInfo(
            name="test-server",
            version="1.0.0",
            capabilities={"tools": True, "resources": True},
        )
        assert info.name == "test-server"
        assert info.version == "1.0.0"
        assert info.capabilities == {"tools": True, "resources": True}

    def test_frozen(self):
        info = McpServerInfo(name="s", version="1", capabilities={})
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            info.name = "other"  # type: ignore

    def test_is_dataclass(self):
        assert dataclasses.is_dataclass(McpServerInfo)
