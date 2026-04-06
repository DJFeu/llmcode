"""Tests for HarnessEngine orchestration."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from llm_code.harness.config import HarnessConfig, HarnessControl


def _make_engine(tmp_path: Path, controls: tuple[HarnessControl, ...] | None = None):
    from llm_code.harness.engine import HarnessEngine

    cfg = HarnessConfig(template="python-cli", controls=controls or ())
    return HarnessEngine(config=cfg, cwd=tmp_path)


def test_engine_init(tmp_path: Path):
    engine = _make_engine(tmp_path)
    assert engine.config.template == "python-cli"


def test_engine_pre_turn_injects_repo_map(tmp_path: Path):
    from llm_code.harness.engine import HarnessEngine

    controls = (
        HarnessControl(name="repo_map", category="guide", kind="computational", trigger="pre_turn"),
    )
    engine = HarnessEngine(config=HarnessConfig(controls=controls), cwd=tmp_path)

    (tmp_path / "main.py").write_text("def greet():\n    pass\n")

    injections = engine.pre_turn()
    assert isinstance(injections, list)
    combined = "\n".join(injections)
    assert "greet" in combined


def test_engine_pre_turn_skips_disabled_guide(tmp_path: Path):
    from llm_code.harness.engine import HarnessEngine

    controls = (
        HarnessControl(name="repo_map", category="guide", kind="computational", trigger="pre_turn", enabled=False),
    )
    engine = HarnessEngine(config=HarnessConfig(controls=controls), cwd=tmp_path)
    (tmp_path / "main.py").write_text("def greet():\n    pass\n")

    injections = engine.pre_turn()
    assert injections == []


def test_engine_pre_turn_analysis_context(tmp_path: Path):
    from llm_code.harness.engine import HarnessEngine

    controls = (
        HarnessControl(name="analysis_context", category="guide", kind="computational", trigger="pre_turn"),
    )
    engine = HarnessEngine(config=HarnessConfig(controls=controls), cwd=tmp_path)
    engine.analysis_context = "[Code Analysis] 2 violations"

    injections = engine.pre_turn()
    assert "[Code Analysis] 2 violations" in injections


def test_engine_plan_mode_denies_write(tmp_path: Path):
    from llm_code.harness.engine import HarnessEngine

    controls = (
        HarnessControl(name="plan_mode", category="guide", kind="computational", trigger="pre_tool"),
    )
    engine = HarnessEngine(config=HarnessConfig(controls=controls), cwd=tmp_path)
    engine.plan_mode = True

    denied = engine.check_pre_tool("write_file")
    assert denied is not None


def test_engine_plan_mode_allows_read(tmp_path: Path):
    from llm_code.harness.engine import HarnessEngine

    controls = (
        HarnessControl(name="plan_mode", category="guide", kind="computational", trigger="pre_tool"),
    )
    engine = HarnessEngine(config=HarnessConfig(controls=controls), cwd=tmp_path)
    engine.plan_mode = True

    denied = engine.check_pre_tool("read_file")
    assert denied is None


@pytest.mark.asyncio
async def test_engine_post_tool_runs_sensors(tmp_path: Path):
    from llm_code.harness.config import HarnessFinding
    from llm_code.harness.engine import HarnessEngine

    controls = (
        HarnessControl(name="lsp_diagnose", category="sensor", kind="computational", trigger="post_tool"),
    )
    engine = HarnessEngine(config=HarnessConfig(controls=controls), cwd=tmp_path)
    engine.lsp_manager = MagicMock()

    with patch("llm_code.harness.engine.lsp_diagnose_sensor", new_callable=AsyncMock) as mock_sensor:
        mock_sensor.return_value = [
            HarnessFinding(sensor="lsp_diagnose", message="error", file_path="foo.py", severity="error")
        ]
        findings = await engine.post_tool(tool_name="write_file", file_path="foo.py", is_error=False)

    assert len(findings) == 1
    assert findings[0].sensor == "lsp_diagnose"


@pytest.mark.asyncio
async def test_engine_post_tool_skips_on_error(tmp_path: Path):
    from llm_code.harness.engine import HarnessEngine

    controls = (
        HarnessControl(name="lsp_diagnose", category="sensor", kind="computational", trigger="post_tool"),
    )
    engine = HarnessEngine(config=HarnessConfig(controls=controls), cwd=tmp_path)

    findings = await engine.post_tool(tool_name="write_file", file_path="foo.py", is_error=True)
    assert findings == []


@pytest.mark.asyncio
async def test_engine_post_tool_only_for_write_tools(tmp_path: Path):
    from llm_code.harness.engine import HarnessEngine

    controls = (
        HarnessControl(name="lsp_diagnose", category="sensor", kind="computational", trigger="post_tool"),
    )
    engine = HarnessEngine(config=HarnessConfig(controls=controls), cwd=tmp_path)

    findings = await engine.post_tool(tool_name="read_file", file_path="foo.py", is_error=False)
    assert findings == []


def test_engine_status(tmp_path: Path):
    from llm_code.harness.engine import HarnessEngine

    controls = (
        HarnessControl(name="repo_map", category="guide", kind="computational", trigger="pre_turn"),
        HarnessControl(name="lsp_diagnose", category="sensor", kind="computational", trigger="post_tool"),
    )
    engine = HarnessEngine(config=HarnessConfig(template="python-cli", controls=controls), cwd=tmp_path)

    status = engine.status()
    assert status["template"] == "python-cli"
    assert len(status["guides"]) == 1
    assert len(status["sensors"]) == 1
    assert status["guides"][0]["name"] == "repo_map"
    assert status["sensors"][0]["name"] == "lsp_diagnose"


def test_engine_enable_disable(tmp_path: Path):
    from llm_code.harness.engine import HarnessEngine

    controls = (
        HarnessControl(name="auto_commit", category="sensor", kind="computational", trigger="post_tool"),
    )
    engine = HarnessEngine(config=HarnessConfig(controls=controls), cwd=tmp_path)

    engine.disable("auto_commit")
    status = engine.status()
    assert status["sensors"][0]["enabled"] is False

    engine.enable("auto_commit")
    status = engine.status()
    assert status["sensors"][0]["enabled"] is True


def test_engine_pre_turn_knowledge_guide(tmp_path: Path):
    from llm_code.harness.engine import HarnessEngine

    controls = (
        HarnessControl(name="knowledge", category="guide", kind="computational", trigger="pre_turn"),
    )
    engine = HarnessEngine(config=HarnessConfig(controls=controls), cwd=tmp_path)

    knowledge_dir = tmp_path / ".llm-code" / "knowledge"
    knowledge_dir.mkdir(parents=True)
    (knowledge_dir / "modules").mkdir()
    (knowledge_dir / "index.md").write_text("# Knowledge Index\n\n- [Api](modules/api.md) — REST API\n")
    (knowledge_dir / "modules" / "api.md").write_text("# API\n\nHandles requests.\n")

    injections = engine.pre_turn()
    combined = "\n".join(injections)
    assert "API" in combined
