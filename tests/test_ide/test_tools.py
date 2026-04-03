"""Tests for IDE tools."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from llm_code.ide.bridge import IDEBridge
from llm_code.tools.base import PermissionLevel
from llm_code.tools.ide_open import IDEOpenTool
from llm_code.tools.ide_diagnostics import IDEDiagnosticsTool
from llm_code.tools.ide_selection import IDESelectionTool


@pytest.fixture
def mock_bridge():
    bridge = MagicMock(spec=IDEBridge)
    bridge.is_connected = True
    bridge.open_file = AsyncMock(return_value=True)
    bridge.get_diagnostics = AsyncMock(return_value=[
        {"line": 5, "severity": "error", "message": "syntax error", "source": "pyright"},
    ])
    bridge.get_selection = AsyncMock(return_value={
        "path": "/tmp/foo.py",
        "start_line": 10,
        "end_line": 15,
        "text": "selected code",
    })
    return bridge


@pytest.fixture
def disconnected_bridge():
    bridge = MagicMock(spec=IDEBridge)
    bridge.is_connected = False
    bridge.open_file = AsyncMock(return_value=False)
    bridge.get_diagnostics = AsyncMock(return_value=[])
    bridge.get_selection = AsyncMock(return_value=None)
    return bridge


class TestIDEOpenTool:
    def test_name(self, mock_bridge):
        tool = IDEOpenTool(mock_bridge)
        assert tool.name == "ide_open"

    def test_permission(self, mock_bridge):
        tool = IDEOpenTool(mock_bridge)
        assert tool.required_permission == PermissionLevel.READ_ONLY

    def test_is_read_only(self, mock_bridge):
        tool = IDEOpenTool(mock_bridge)
        assert tool.is_read_only({}) is True

    def test_execute_success(self, mock_bridge):
        tool = IDEOpenTool(mock_bridge)
        result = tool.execute({"path": "/tmp/foo.py", "line": 42})
        assert not result.is_error
        assert "opened" in result.output.lower() or "foo.py" in result.output

    def test_execute_not_connected(self, disconnected_bridge):
        tool = IDEOpenTool(disconnected_bridge)
        result = tool.execute({"path": "/tmp/foo.py"})
        assert result.is_error
        assert "not connected" in result.output.lower() or "no ide" in result.output.lower()


class TestIDEDiagnosticsTool:
    def test_name(self, mock_bridge):
        tool = IDEDiagnosticsTool(mock_bridge)
        assert tool.name == "ide_diagnostics"

    def test_permission(self, mock_bridge):
        tool = IDEDiagnosticsTool(mock_bridge)
        assert tool.required_permission == PermissionLevel.READ_ONLY

    def test_execute_returns_diagnostics(self, mock_bridge):
        tool = IDEDiagnosticsTool(mock_bridge)
        result = tool.execute({"path": "/tmp/foo.py"})
        assert not result.is_error
        assert "syntax error" in result.output

    def test_execute_no_diagnostics(self, disconnected_bridge):
        tool = IDEDiagnosticsTool(disconnected_bridge)
        result = tool.execute({"path": "/tmp/foo.py"})
        assert not result.is_error
        assert "no diagnostics" in result.output.lower() or "0" in result.output


class TestIDESelectionTool:
    def test_name(self, mock_bridge):
        tool = IDESelectionTool(mock_bridge)
        assert tool.name == "ide_selection"

    def test_permission(self, mock_bridge):
        tool = IDESelectionTool(mock_bridge)
        assert tool.required_permission == PermissionLevel.READ_ONLY

    def test_execute_returns_selection(self, mock_bridge):
        tool = IDESelectionTool(mock_bridge)
        result = tool.execute({})
        assert not result.is_error
        assert "selected code" in result.output

    def test_execute_no_selection(self, disconnected_bridge):
        tool = IDESelectionTool(disconnected_bridge)
        result = tool.execute({})
        assert not result.is_error
        assert "no selection" in result.output.lower() or "nothing" in result.output.lower()
