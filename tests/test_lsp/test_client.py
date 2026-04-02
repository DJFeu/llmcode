"""Tests for LSP client (Task 1) — uses MockTransport."""
from __future__ import annotations

import json
from typing import Any

import pytest

from llm_code.lsp.client import (
    Diagnostic,
    Location,
    LspClient,
    LspServerConfig,
    LspTransport,
)


class MockLspTransport(LspTransport):
    """In-memory LSP transport for testing."""

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.sent: list[dict[str, Any]] = []
        self._responses = list(responses)
        self.closed = False

    async def start(self) -> None:
        pass

    async def send_message(self, message: dict[str, Any]) -> None:
        self.sent.append(message)

    async def receive_message(self) -> dict[str, Any]:
        if not self._responses:
            raise RuntimeError("No more mock responses")
        return self._responses.pop(0)

    async def close(self) -> None:
        self.closed = True


def make_lsp_response(request_id: int, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def make_lsp_error(request_id: int, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


class TestLspClientDataclasses:
    def test_location_frozen(self):
        loc = Location(file="foo.py", line=1, column=5)
        with pytest.raises((AttributeError, TypeError)):
            loc.line = 2  # type: ignore[misc]

    def test_diagnostic_frozen(self):
        d = Diagnostic(
            file="foo.py",
            line=1,
            column=0,
            severity="error",
            message="undefined",
            source="pyright",
        )
        with pytest.raises((AttributeError, TypeError)):
            d.message = "other"  # type: ignore[misc]

    def test_lsp_server_config_defaults(self):
        cfg = LspServerConfig(command="pyright-langserver")
        assert cfg.args == ()
        assert cfg.language == ""


class TestLspClientInitialize:
    @pytest.mark.asyncio
    async def test_initialize_sends_request(self):
        transport = MockLspTransport(
            [
                make_lsp_response(
                    1,
                    {
                        "capabilities": {"textDocumentSync": 1},
                        "serverInfo": {"name": "pyright", "version": "1.0"},
                    },
                )
            ]
        )
        client = LspClient(transport)
        result = await client.initialize("file:///workspace")

        assert len(transport.sent) == 1
        msg = transport.sent[0]
        assert msg["method"] == "initialize"
        assert msg["params"]["rootUri"] == "file:///workspace"

    @pytest.mark.asyncio
    async def test_initialize_returns_capabilities(self):
        transport = MockLspTransport(
            [
                make_lsp_response(
                    1,
                    {
                        "capabilities": {"hoverProvider": True},
                        "serverInfo": {"name": "test-server"},
                    },
                )
            ]
        )
        client = LspClient(transport)
        result = await client.initialize("file:///proj")

        assert isinstance(result, dict)
        assert "capabilities" in result


class TestLspClientGotoDefinition:
    @pytest.mark.asyncio
    async def test_goto_definition_returns_locations(self):
        transport = MockLspTransport(
            [
                make_lsp_response(
                    1,
                    [
                        {
                            "uri": "file:///src/foo.py",
                            "range": {
                                "start": {"line": 10, "character": 4},
                                "end": {"line": 10, "character": 10},
                            },
                        }
                    ],
                )
            ]
        )
        client = LspClient(transport)
        locations = await client.goto_definition("file:///test.py", 5, 3)

        assert len(locations) == 1
        loc = locations[0]
        assert isinstance(loc, Location)
        assert loc.file == "file:///src/foo.py"
        assert loc.line == 10
        assert loc.column == 4

    @pytest.mark.asyncio
    async def test_goto_definition_sends_correct_method(self):
        transport = MockLspTransport([make_lsp_response(1, [])])
        client = LspClient(transport)
        await client.goto_definition("file:///test.py", 2, 7)

        msg = transport.sent[0]
        assert msg["method"] == "textDocument/definition"
        assert msg["params"]["textDocument"]["uri"] == "file:///test.py"
        assert msg["params"]["position"]["line"] == 2
        assert msg["params"]["position"]["character"] == 7

    @pytest.mark.asyncio
    async def test_goto_definition_empty_result(self):
        transport = MockLspTransport([make_lsp_response(1, None)])
        client = LspClient(transport)
        locations = await client.goto_definition("file:///x.py", 0, 0)
        assert locations == []

    @pytest.mark.asyncio
    async def test_goto_definition_single_location_object(self):
        """LSP may return a single Location object (not array)."""
        transport = MockLspTransport(
            [
                make_lsp_response(
                    1,
                    {
                        "uri": "file:///bar.py",
                        "range": {
                            "start": {"line": 3, "character": 0},
                            "end": {"line": 3, "character": 5},
                        },
                    },
                )
            ]
        )
        client = LspClient(transport)
        locations = await client.goto_definition("file:///test.py", 0, 0)
        assert len(locations) == 1
        assert locations[0].file == "file:///bar.py"


class TestLspClientFindReferences:
    @pytest.mark.asyncio
    async def test_find_references_returns_locations(self):
        transport = MockLspTransport(
            [
                make_lsp_response(
                    1,
                    [
                        {
                            "uri": "file:///a.py",
                            "range": {
                                "start": {"line": 1, "character": 2},
                                "end": {"line": 1, "character": 8},
                            },
                        },
                        {
                            "uri": "file:///b.py",
                            "range": {
                                "start": {"line": 20, "character": 0},
                                "end": {"line": 20, "character": 5},
                            },
                        },
                    ],
                )
            ]
        )
        client = LspClient(transport)
        refs = await client.find_references("file:///src.py", 5, 10)

        assert len(refs) == 2
        assert refs[0].file == "file:///a.py"
        assert refs[1].file == "file:///b.py"

    @pytest.mark.asyncio
    async def test_find_references_sends_include_declaration(self):
        transport = MockLspTransport([make_lsp_response(1, [])])
        client = LspClient(transport)
        await client.find_references("file:///src.py", 3, 5)

        msg = transport.sent[0]
        assert msg["method"] == "textDocument/references"
        assert msg["params"]["context"]["includeDeclaration"] is True


class TestLspClientGetDiagnostics:
    @pytest.mark.asyncio
    async def test_get_diagnostics_returns_diagnostics(self):
        transport = MockLspTransport(
            [
                make_lsp_response(
                    1,
                    {
                        "items": [
                            {
                                "uri": "file:///foo.py",
                                "diagnostics": [
                                    {
                                        "range": {
                                            "start": {"line": 5, "character": 3}
                                        },
                                        "severity": 1,
                                        "message": "Cannot find name 'x'",
                                        "source": "pyright",
                                    }
                                ],
                            }
                        ]
                    },
                )
            ]
        )
        client = LspClient(transport)
        diags = await client.get_diagnostics("file:///foo.py")

        assert len(diags) == 1
        d = diags[0]
        assert isinstance(d, Diagnostic)
        assert d.file == "file:///foo.py"
        assert d.line == 5
        assert d.column == 3
        assert d.severity == "error"
        assert d.message == "Cannot find name 'x'"
        assert d.source == "pyright"

    @pytest.mark.asyncio
    async def test_get_diagnostics_severity_mapping(self):
        """LSP severity: 1=error, 2=warning, 3=info, 4=hint."""
        transport = MockLspTransport(
            [
                make_lsp_response(
                    1,
                    {
                        "items": [
                            {
                                "uri": "file:///f.py",
                                "diagnostics": [
                                    {
                                        "range": {"start": {"line": 0, "character": 0}},
                                        "severity": 2,
                                        "message": "warn",
                                        "source": "ruff",
                                    },
                                    {
                                        "range": {"start": {"line": 1, "character": 0}},
                                        "severity": 3,
                                        "message": "info",
                                        "source": "ruff",
                                    },
                                    {
                                        "range": {"start": {"line": 2, "character": 0}},
                                        "severity": 4,
                                        "message": "hint",
                                        "source": "ruff",
                                    },
                                ],
                            }
                        ]
                    },
                )
            ]
        )
        client = LspClient(transport)
        diags = await client.get_diagnostics("file:///f.py")
        assert diags[0].severity == "warning"
        assert diags[1].severity == "info"
        assert diags[2].severity == "hint"

    @pytest.mark.asyncio
    async def test_get_diagnostics_empty(self):
        transport = MockLspTransport(
            [make_lsp_response(1, {"items": []})]
        )
        client = LspClient(transport)
        diags = await client.get_diagnostics("file:///clean.py")
        assert diags == []


class TestLspClientDidOpen:
    @pytest.mark.asyncio
    async def test_did_open_sends_notification(self):
        transport = MockLspTransport([])
        client = LspClient(transport)
        await client.did_open("file:///test.py", "x = 1\n")

        assert len(transport.sent) == 1
        msg = transport.sent[0]
        assert msg["method"] == "textDocument/didOpen"
        assert "id" not in msg  # notification, no id
        assert msg["params"]["textDocument"]["uri"] == "file:///test.py"
        assert msg["params"]["textDocument"]["text"] == "x = 1\n"

    @pytest.mark.asyncio
    async def test_did_open_returns_none(self):
        transport = MockLspTransport([])
        client = LspClient(transport)
        result = await client.did_open("file:///test.py", "")
        assert result is None


class TestLspClientShutdown:
    @pytest.mark.asyncio
    async def test_shutdown_sends_request_then_exit(self):
        transport = MockLspTransport([make_lsp_response(1, None)])
        client = LspClient(transport)
        await client.shutdown()

        # Should have sent shutdown request + exit notification
        methods = [m["method"] for m in transport.sent]
        assert "shutdown" in methods
        assert "exit" in methods

    @pytest.mark.asyncio
    async def test_shutdown_closes_transport(self):
        transport = MockLspTransport([make_lsp_response(1, None)])
        client = LspClient(transport)
        await client.shutdown()
        assert transport.closed is True


class TestLspClientErrorHandling:
    @pytest.mark.asyncio
    async def test_error_response_raises_runtime_error(self):
        transport = MockLspTransport([make_lsp_error(1, -32601, "Method not found")])
        client = LspClient(transport)
        with pytest.raises(RuntimeError, match="Method not found"):
            await client.goto_definition("file:///x.py", 0, 0)
