"""Project-type detection and default harness templates."""
from __future__ import annotations

from pathlib import Path

from llm_code.harness.config import HarnessControl

_WEB_FRAMEWORKS = ("fastapi", "flask", "django", "starlette", "sanic")


def detect_template(cwd: Path) -> str:
    """Auto-detect project type from files in *cwd*."""
    if (cwd / "pnpm-workspace.yaml").exists() or (cwd / "turbo.json").exists():
        return "monorepo"

    if (cwd / "manage.py").exists():
        return "python-web"

    has_pyproject = (cwd / "pyproject.toml").exists()
    has_requirements = (cwd / "requirements.txt").exists()

    if has_pyproject or has_requirements:
        if _has_web_dep(cwd):
            return "python-web"
        return "python-cli"

    if (cwd / "package.json").exists():
        return "node-app"

    return "generic"


def _has_web_dep(cwd: Path) -> bool:
    for fname in ("pyproject.toml", "requirements.txt", "setup.cfg"):
        fpath = cwd / fname
        if fpath.exists():
            try:
                content = fpath.read_text(encoding="utf-8").lower()
                if any(fw in content for fw in _WEB_FRAMEWORKS):
                    return True
            except OSError:
                pass
    return False


def default_controls(template: str) -> tuple[HarnessControl, ...]:
    _TEMPLATES: dict[str, tuple[HarnessControl, ...]] = {
        "python-cli": (
            HarnessControl(name="repo_map", category="guide", kind="computational", trigger="pre_turn"),
            HarnessControl(name="analysis_context", category="guide", kind="computational", trigger="pre_turn"),
            HarnessControl(name="knowledge", category="guide", kind="computational", trigger="pre_turn"),
            HarnessControl(name="plan_mode", category="guide", kind="computational", trigger="pre_tool"),
            HarnessControl(name="lsp_diagnose", category="sensor", kind="computational", trigger="post_tool"),
            HarnessControl(name="code_rules", category="sensor", kind="computational", trigger="post_tool"),
            HarnessControl(name="auto_commit", category="sensor", kind="computational", trigger="post_tool"),
        ),
        "python-web": (
            HarnessControl(name="repo_map", category="guide", kind="computational", trigger="pre_turn"),
            HarnessControl(name="architecture_doc", category="guide", kind="computational", trigger="pre_turn"),
            HarnessControl(name="analysis_context", category="guide", kind="computational", trigger="pre_turn"),
            HarnessControl(name="knowledge", category="guide", kind="computational", trigger="pre_turn"),
            HarnessControl(name="plan_mode", category="guide", kind="computational", trigger="pre_tool"),
            HarnessControl(name="lsp_diagnose", category="sensor", kind="computational", trigger="post_tool"),
            HarnessControl(name="code_rules", category="sensor", kind="computational", trigger="post_tool"),
            HarnessControl(name="auto_commit", category="sensor", kind="computational", trigger="post_tool"),
        ),
        "node-app": (
            HarnessControl(name="repo_map", category="guide", kind="computational", trigger="pre_turn"),
            HarnessControl(name="plan_mode", category="guide", kind="computational", trigger="pre_tool"),
            HarnessControl(name="code_rules", category="sensor", kind="computational", trigger="post_tool"),
        ),
        "monorepo": (
            HarnessControl(name="repo_map", category="guide", kind="computational", trigger="pre_turn"),
            HarnessControl(name="architecture_doc", category="guide", kind="computational", trigger="pre_turn"),
            HarnessControl(name="plan_mode", category="guide", kind="computational", trigger="pre_tool"),
            HarnessControl(name="code_rules", category="sensor", kind="computational", trigger="post_tool"),
        ),
        "generic": (
            HarnessControl(name="repo_map", category="guide", kind="computational", trigger="pre_turn"),
            HarnessControl(name="plan_mode", category="guide", kind="computational", trigger="pre_tool"),
            HarnessControl(name="code_rules", category="sensor", kind="computational", trigger="post_tool"),
        ),
    }
    return _TEMPLATES.get(template, _TEMPLATES["generic"])
