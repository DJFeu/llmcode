"""Tests for harness template detection."""
from __future__ import annotations

from pathlib import Path


def test_detect_python_cli(tmp_path: Path):
    from llm_code.harness.templates import detect_template

    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'mycli'\n")
    (tmp_path / "main.py").write_text("print('hello')\n")

    tpl = detect_template(tmp_path)
    assert tpl == "python-cli"


def test_detect_python_web_fastapi(tmp_path: Path):
    from llm_code.harness.templates import detect_template

    (tmp_path / "pyproject.toml").write_text('[project]\ndependencies = ["fastapi"]\n')
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "__init__.py").write_text("")

    tpl = detect_template(tmp_path)
    assert tpl == "python-web"


def test_detect_python_web_flask(tmp_path: Path):
    from llm_code.harness.templates import detect_template

    (tmp_path / "requirements.txt").write_text("flask>=2.0\n")

    tpl = detect_template(tmp_path)
    assert tpl == "python-web"


def test_detect_python_web_django(tmp_path: Path):
    from llm_code.harness.templates import detect_template

    (tmp_path / "manage.py").write_text("#!/usr/bin/env python\n")

    tpl = detect_template(tmp_path)
    assert tpl == "python-web"


def test_detect_node_app(tmp_path: Path):
    from llm_code.harness.templates import detect_template

    (tmp_path / "package.json").write_text('{"name": "myapp"}\n')

    tpl = detect_template(tmp_path)
    assert tpl == "node-app"


def test_detect_monorepo_pnpm(tmp_path: Path):
    from llm_code.harness.templates import detect_template

    (tmp_path / "pnpm-workspace.yaml").write_text("packages:\n  - 'packages/*'\n")

    tpl = detect_template(tmp_path)
    assert tpl == "monorepo"


def test_detect_monorepo_turbo(tmp_path: Path):
    from llm_code.harness.templates import detect_template

    (tmp_path / "turbo.json").write_text('{"pipeline": {}}\n')

    tpl = detect_template(tmp_path)
    assert tpl == "monorepo"


def test_detect_generic_fallback(tmp_path: Path):
    from llm_code.harness.templates import detect_template

    tpl = detect_template(tmp_path)
    assert tpl == "generic"


def test_default_controls_python_cli():
    from llm_code.harness.templates import default_controls

    controls = default_controls("python-cli")
    names = {c.name for c in controls}
    assert "repo_map" in names
    assert "analysis_context" in names
    assert "lsp_diagnose" in names
    assert "code_rules" in names
    assert "auto_commit" in names
    test_runner = [c for c in controls if c.name == "test_runner"]
    assert len(test_runner) == 0 or not test_runner[0].enabled


def test_default_controls_python_web():
    from llm_code.harness.templates import default_controls

    controls = default_controls("python-web")
    names = {c.name for c in controls}
    assert "repo_map" in names
    assert "architecture_doc" in names
    assert "lsp_diagnose" in names
    assert "code_rules" in names


def test_default_controls_generic():
    from llm_code.harness.templates import default_controls

    controls = default_controls("generic")
    names = {c.name for c in controls}
    assert "repo_map" in names
    assert "code_rules" in names
