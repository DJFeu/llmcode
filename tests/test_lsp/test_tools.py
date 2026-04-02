"""Tests for LSP tools (Task 4)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from llm_code.lsp.client import Diagnostic, Location
from llm_code.lsp.tools import (
    LspDiagnosticsTool,
    LspFindReferencesTool,
    LspGotoDefinitionTool,
)


def make_mock_manager(client=None):
    manager = MagicMock()
    manager.get_client = MagicMock(return_value=client)
    return manager


def make_mock_client():
    client = MagicMock()
    client.goto_definition = AsyncMock()
    client.find_references = AsyncMock()
    client.get_diagnostics = AsyncMock()
    return client


class TestLspGotoDefinitionTool:
    def test_name(self):
        tool = LspGotoDefinitionTool(make_mock_manager())
        assert tool.name == "lsp_goto_definition"

    def test_is_read_only(self):
        tool = LspGotoDefinitionTool(make_mock_manager())
        assert tool.is_read_only({}) is True

    def test_is_concurrency_safe(self):
        tool = LspGotoDefinitionTool(make_mock_manager())
        assert tool.is_concurrency_safe({}) is True

    @pytest.mark.asyncio
    async def test_returns_formatted_location(self):
        client = make_mock_client()
        client.goto_definition.return_value = [
            Location(file="file:///src/foo.py", line=10, column=4)
        ]
        manager = make_mock_manager(client)
        tool = LspGotoDefinitionTool(manager)

        result = await tool.execute_async(
            {"file": "/workspace/test.py", "line": 5, "column": 3}
        )
        assert "foo.py" in result.output or "file:///src/foo.py" in result.output
        assert "10" in result.output
        assert result.is_error is False

    @pytest.mark.asyncio
    async def test_no_client_returns_error(self):
        manager = make_mock_manager(None)
        tool = LspGotoDefinitionTool(manager)
        result = await tool.execute_async(
            {"file": "/workspace/test.py", "line": 1, "column": 0}
        )
        assert result.is_error is True

    @pytest.mark.asyncio
    async def test_empty_locations_message(self):
        client = make_mock_client()
        client.goto_definition.return_value = []
        manager = make_mock_manager(client)
        tool = LspGotoDefinitionTool(manager)

        result = await tool.execute_async(
            {"file": "/workspace/test.py", "line": 1, "column": 0}
        )
        assert result.is_error is False
        assert "No definition" in result.output or len(result.output) > 0

    @pytest.mark.asyncio
    async def test_detects_language_from_extension(self):
        client = make_mock_client()
        client.goto_definition.return_value = []
        manager = make_mock_manager(client)
        tool = LspGotoDefinitionTool(manager)

        await tool.execute_async(
            {"file": "/proj/main.py", "line": 0, "column": 0}
        )
        manager.get_client.assert_called_once_with("python")

    @pytest.mark.asyncio
    async def test_ts_extension_uses_typescript_client(self):
        client = make_mock_client()
        client.goto_definition.return_value = []
        manager = make_mock_manager(client)
        tool = LspGotoDefinitionTool(manager)

        await tool.execute_async(
            {"file": "/proj/app.ts", "line": 0, "column": 0}
        )
        manager.get_client.assert_called_once_with("typescript")


class TestLspFindReferencesTool:
    def test_name(self):
        tool = LspFindReferencesTool(make_mock_manager())
        assert tool.name == "lsp_find_references"

    @pytest.mark.asyncio
    async def test_returns_formatted_references(self):
        client = make_mock_client()
        client.find_references.return_value = [
            Location(file="file:///a.py", line=1, column=2),
            Location(file="file:///b.py", line=20, column=0),
        ]
        manager = make_mock_manager(client)
        tool = LspFindReferencesTool(manager)

        result = await tool.execute_async(
            {"file": "/proj/src.py", "line": 5, "column": 10}
        )
        assert result.is_error is False
        assert "a.py" in result.output or "file:///a.py" in result.output

    @pytest.mark.asyncio
    async def test_no_client_returns_error(self):
        manager = make_mock_manager(None)
        tool = LspFindReferencesTool(manager)
        result = await tool.execute_async(
            {"file": "/proj/src.go", "line": 1, "column": 0}
        )
        assert result.is_error is True

    @pytest.mark.asyncio
    async def test_go_extension_uses_go_client(self):
        client = make_mock_client()
        client.find_references.return_value = []
        manager = make_mock_manager(client)
        tool = LspFindReferencesTool(manager)

        await tool.execute_async(
            {"file": "/proj/main.go", "line": 3, "column": 5}
        )
        manager.get_client.assert_called_once_with("go")


class TestLspDiagnosticsTool:
    def test_name(self):
        tool = LspDiagnosticsTool(make_mock_manager())
        assert tool.name == "lsp_diagnostics"

    @pytest.mark.asyncio
    async def test_returns_formatted_diagnostics(self):
        client = make_mock_client()
        client.get_diagnostics.return_value = [
            Diagnostic(
                file="file:///foo.py",
                line=5,
                column=3,
                severity="error",
                message="Cannot find name 'x'",
                source="pyright",
            )
        ]
        manager = make_mock_manager(client)
        tool = LspDiagnosticsTool(manager)

        result = await tool.execute_async({"file": "/proj/foo.py"})
        assert result.is_error is False
        assert "error" in result.output.lower() or "Cannot find" in result.output
        assert "5" in result.output

    @pytest.mark.asyncio
    async def test_no_client_returns_error(self):
        manager = make_mock_manager(None)
        tool = LspDiagnosticsTool(manager)
        result = await tool.execute_async({"file": "/proj/foo.rs"})
        assert result.is_error is True

    @pytest.mark.asyncio
    async def test_no_diagnostics_clean_message(self):
        client = make_mock_client()
        client.get_diagnostics.return_value = []
        manager = make_mock_manager(client)
        tool = LspDiagnosticsTool(manager)

        result = await tool.execute_async({"file": "/proj/clean.py"})
        assert result.is_error is False
        assert len(result.output) > 0

    @pytest.mark.asyncio
    async def test_rust_extension_uses_rust_client(self):
        client = make_mock_client()
        client.get_diagnostics.return_value = []
        manager = make_mock_manager(client)
        tool = LspDiagnosticsTool(manager)

        await tool.execute_async({"file": "/proj/main.rs"})
        manager.get_client.assert_called_once_with("rust")

    @pytest.mark.asyncio
    async def test_multiple_diagnostics_all_shown(self):
        client = make_mock_client()
        client.get_diagnostics.return_value = [
            Diagnostic(
                file="file:///f.py",
                line=1,
                column=0,
                severity="error",
                message="error one",
                source="pyright",
            ),
            Diagnostic(
                file="file:///f.py",
                line=2,
                column=0,
                severity="warning",
                message="warn two",
                source="pyright",
            ),
        ]
        manager = make_mock_manager(client)
        tool = LspDiagnosticsTool(manager)

        result = await tool.execute_async({"file": "/proj/f.py"})
        assert "error one" in result.output
        assert "warn two" in result.output


class TestLspToolsInputSchema:
    def test_goto_definition_schema(self):
        tool = LspGotoDefinitionTool(make_mock_manager())
        schema = tool.input_schema
        assert schema["type"] == "object"
        props = schema["properties"]
        assert "file" in props
        assert "line" in props
        assert "column" in props

    def test_find_references_schema(self):
        tool = LspFindReferencesTool(make_mock_manager())
        schema = tool.input_schema
        props = schema["properties"]
        assert "file" in props
        assert "line" in props
        assert "column" in props

    def test_diagnostics_schema(self):
        tool = LspDiagnosticsTool(make_mock_manager())
        schema = tool.input_schema
        props = schema["properties"]
        assert "file" in props
