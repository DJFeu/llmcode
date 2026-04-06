"""Tests for harness sensor implementations."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_lsp_diagnose_sensor_returns_findings():
    from llm_code.harness.config import HarnessFinding
    from llm_code.harness.sensors import lsp_diagnose_sensor

    with patch("llm_code.harness.sensors.auto_diagnose", new_callable=AsyncMock) as mock_diag:
        mock_diag.return_value = ["foo.py:1:0 [error] type mismatch (pyright)"]
        findings = await lsp_diagnose_sensor(lsp_manager=MagicMock(), file_path="foo.py")

    assert len(findings) == 1
    assert isinstance(findings[0], HarnessFinding)
    assert findings[0].sensor == "lsp_diagnose"
    assert "type mismatch" in findings[0].message
    assert findings[0].file_path == "foo.py"
    assert findings[0].severity == "error"


@pytest.mark.asyncio
async def test_lsp_diagnose_sensor_empty():
    from llm_code.harness.sensors import lsp_diagnose_sensor

    with patch("llm_code.harness.sensors.auto_diagnose", new_callable=AsyncMock) as mock_diag:
        mock_diag.return_value = []
        findings = await lsp_diagnose_sensor(lsp_manager=MagicMock(), file_path="foo.py")

    assert findings == []


@pytest.mark.asyncio
async def test_lsp_diagnose_sensor_no_manager():
    from llm_code.harness.sensors import lsp_diagnose_sensor

    findings = await lsp_diagnose_sensor(lsp_manager=None, file_path="foo.py")
    assert findings == []


@pytest.mark.asyncio
async def test_lsp_diagnose_sensor_handles_errors():
    from llm_code.harness.sensors import lsp_diagnose_sensor

    with patch("llm_code.harness.sensors.auto_diagnose", new_callable=AsyncMock) as mock_diag:
        mock_diag.side_effect = RuntimeError("boom")
        findings = await lsp_diagnose_sensor(lsp_manager=MagicMock(), file_path="foo.py")

    assert findings == []


def test_code_rules_sensor_returns_findings(tmp_path: Path):
    from llm_code.harness.config import HarnessFinding
    from llm_code.harness.sensors import code_rules_sensor
    from llm_code.analysis.rules import Violation, AnalysisResult

    fake_result = AnalysisResult(
        violations=(Violation(rule_key="too-long", severity="medium", file_path="foo.py", line=10, message="File too long"),),
        file_count=1,
        duration_ms=5.0,
    )
    with patch("llm_code.harness.sensors.run_analysis", return_value=fake_result):
        findings = code_rules_sensor(cwd=tmp_path, file_path="foo.py")

    assert len(findings) == 1
    assert isinstance(findings[0], HarnessFinding)
    assert findings[0].sensor == "code_rules"
    assert "too long" in findings[0].message.lower()


def test_code_rules_sensor_no_violations(tmp_path: Path):
    from llm_code.harness.sensors import code_rules_sensor
    from llm_code.analysis.rules import AnalysisResult

    fake_result = AnalysisResult(violations=(), file_count=1, duration_ms=2.0)
    with patch("llm_code.harness.sensors.run_analysis", return_value=fake_result):
        findings = code_rules_sensor(cwd=tmp_path, file_path="foo.py")

    assert findings == []


def test_code_rules_sensor_handles_errors(tmp_path: Path):
    from llm_code.harness.sensors import code_rules_sensor

    with patch("llm_code.harness.sensors.run_analysis", side_effect=RuntimeError("boom")):
        findings = code_rules_sensor(cwd=tmp_path, file_path="foo.py")

    assert findings == []


def test_auto_commit_sensor_success(tmp_path: Path):
    from llm_code.harness.config import HarnessFinding
    from llm_code.harness.sensors import auto_commit_sensor

    with patch("llm_code.harness.sensors.auto_commit_file", return_value=True):
        finding = auto_commit_sensor(file_path=Path("foo.py"), tool_name="write_file")

    assert isinstance(finding, HarnessFinding)
    assert finding.sensor == "auto_commit"
    assert finding.severity == "info"


def test_auto_commit_sensor_failure():
    from llm_code.harness.sensors import auto_commit_sensor

    with patch("llm_code.harness.sensors.auto_commit_file", return_value=False):
        finding = auto_commit_sensor(file_path=Path("foo.py"), tool_name="edit_file")

    assert finding is None


def test_auto_commit_sensor_handles_errors():
    from llm_code.harness.sensors import auto_commit_sensor

    with patch("llm_code.harness.sensors.auto_commit_file", side_effect=RuntimeError("boom")):
        finding = auto_commit_sensor(file_path=Path("foo.py"), tool_name="edit_file")

    assert finding is None
