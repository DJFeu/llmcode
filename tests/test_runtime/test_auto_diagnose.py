"""Tests for llm_code.runtime.auto_diagnose -- automatic LSP diagnostics after edit."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from llm_code.runtime.auto_diagnose import auto_diagnose, format_diagnostics


class _FakeDiagnostic:
    def __init__(self, file: str, line: int, column: int, severity: str, message: str, source: str) -> None:
        self.file = file
        self.line = line
        self.column = column
        self.severity = severity
        self.message = message
        self.source = source


class TestAutoDiagnose:
    @pytest.mark.asyncio
    async def test_returns_errors_only(self) -> None:
        mock_client = AsyncMock()
        mock_client.get_diagnostics.return_value = [
            _FakeDiagnostic("app.py", 10, 5, "error", "Name 'foo' is not defined", "pyright"),
            _FakeDiagnostic("app.py", 15, 0, "warning", "Unused import", "pyright"),
            _FakeDiagnostic("app.py", 20, 3, "error", "Unexpected indent", "pyright"),
        ]

        mock_manager = MagicMock()
        mock_manager.get_client.return_value = mock_client

        errors = await auto_diagnose(mock_manager, "app.py")
        assert len(errors) == 2
        assert "foo" in errors[0]
        assert "indent" in errors[1]

    @pytest.mark.asyncio
    async def test_no_errors_returns_empty(self) -> None:
        mock_client = AsyncMock()
        mock_client.get_diagnostics.return_value = [
            _FakeDiagnostic("app.py", 5, 0, "warning", "Unused var", "pyright"),
        ]

        mock_manager = MagicMock()
        mock_manager.get_client.return_value = mock_client

        errors = await auto_diagnose(mock_manager, "app.py")
        assert errors == []

    @pytest.mark.asyncio
    async def test_no_lsp_client_returns_empty(self) -> None:
        mock_manager = MagicMock()
        mock_manager.get_client.return_value = None

        errors = await auto_diagnose(mock_manager, "app.py")
        assert errors == []

    @pytest.mark.asyncio
    async def test_lsp_error_returns_empty(self) -> None:
        mock_client = AsyncMock()
        mock_client.get_diagnostics.side_effect = Exception("LSP crashed")

        mock_manager = MagicMock()
        mock_manager.get_client.return_value = mock_client

        errors = await auto_diagnose(mock_manager, "app.py")
        assert errors == []


class TestFormatDiagnostics:
    def test_format_single_error(self) -> None:
        diag = _FakeDiagnostic("utils.py", 42, 8, "error", "Name 'bar' is not defined", "pyright")
        result = format_diagnostics([diag])
        assert len(result) == 1
        assert "utils.py:42" in result[0]
        assert "bar" in result[0]

    def test_format_empty(self) -> None:
        result = format_diagnostics([])
        assert result == []
