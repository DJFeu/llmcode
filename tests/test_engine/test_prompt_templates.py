"""Template-level tests for engine/prompts/*.j2 (v12 M1.2/M1.3).

For each migrated .j2 template, assert:

1. PromptBuilder can load it.
2. Render succeeds with no required variables (initial migration keeps
   original .md content verbatim — no Jinja directives yet, so no
   variables needed).
3. Output contains no leaked `{{` / `}}` delimiters (catches accidental
   typos that the render step would silently pass through).
4. Output is non-empty for templates that have content; base.j2 is the
   single exception (its blocks are intentionally empty pre-shim).

The migrated templates live under two subdirectories:

- ``modes/``  — mode-specific system-prompt fragments (plan, build_switch,
  max_steps, plan_anthropic).
- ``models/`` — model-family-specific system prompts (anthropic, beast,
  codex, copilot_gpt5, deepseek, default, gemini, gpt, kimi, llama, qwen,
  trinity). All families migrated by v2.0 (M8.b).

Plus ``base.j2`` at the root — the skeleton other templates will extend
once the shim in ``runtime/prompt.py`` routes through PromptBuilder.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from llm_code.engine.prompt_builder import PromptBuilder

PROMPTS_DIR = (
    Path(__file__).resolve().parents[2]
    / "llm_code"
    / "engine"
    / "prompts"
)
ALL_TEMPLATES = sorted(
    str(p.relative_to(PROMPTS_DIR))
    for p in PROMPTS_DIR.rglob("*.j2")
)


class TestTemplateInventory:
    def test_prompts_dir_exists(self) -> None:
        assert PROMPTS_DIR.exists()
        assert PROMPTS_DIR.is_dir()

    def test_at_least_one_template_migrated(self) -> None:
        assert len(ALL_TEMPLATES) >= 8

    def test_required_subdirectories_exist(self) -> None:
        assert (PROMPTS_DIR / "modes").is_dir()
        assert (PROMPTS_DIR / "models").is_dir()
        assert (PROMPTS_DIR / "sections").is_dir()
        assert (PROMPTS_DIR / "reminders").is_dir()

    def test_initial_mode_migrations_present(self) -> None:
        modes = {p.name for p in (PROMPTS_DIR / "modes").glob("*.j2")}
        for expected in (
            "build_switch.j2",
            "max_steps.j2",
            "plan.j2",
            "plan_anthropic.j2",
        ):
            assert expected in modes, f"mode template missing: {expected}"

    def test_initial_model_migrations_present(self) -> None:
        models = {p.name for p in (PROMPTS_DIR / "models").glob("*.j2")}
        for expected in ("beast.j2", "default.j2", "anthropic.j2"):
            assert expected in models, f"model template missing: {expected}"

    def test_base_template_present(self) -> None:
        assert (PROMPTS_DIR / "base.j2").exists()


@pytest.mark.parametrize("rel_path", ALL_TEMPLATES)
class TestEachTemplateRenders:
    def test_loads(self, rel_path: str) -> None:
        builder = PromptBuilder(
            template_path=rel_path, templates_dir=PROMPTS_DIR
        )
        assert builder.template_name == rel_path

    def test_declared_variables_documented(self, rel_path: str) -> None:
        builder = PromptBuilder(
            template_path=rel_path, templates_dir=PROMPTS_DIR
        )
        declared = builder.declared_variables
        assert isinstance(declared, frozenset)
        for v in declared:
            assert isinstance(v, str) and v, f"invalid var name: {v!r}"

    def test_renders_without_variables(self, rel_path: str) -> None:
        builder = PromptBuilder(
            template_path=rel_path, templates_dir=PROMPTS_DIR
        )
        if builder.declared_variables:
            pytest.skip(
                f"template {rel_path} declares variables "
                f"{sorted(builder.declared_variables)}; test_renders_with_"
                f"fixture_state covers it"
            )
        rendered = builder.run()["prompt"]
        assert rendered is not None
        assert "{{" not in rendered, (
            f"{rel_path}: Jinja2 opening delimiter leaked into output"
        )
        assert "}}" not in rendered, (
            f"{rel_path}: Jinja2 closing delimiter leaked into output"
        )


class TestBaseTemplateStructure:
    def test_base_has_expected_blocks(self) -> None:
        content = (PROMPTS_DIR / "base.j2").read_text()
        for block in (
            "capability_intro",
            "tools",
            "memory_context",
            "permission_hint",
            "mode_specific",
            "reminders",
        ):
            assert f"block {block}" in content, (
                f"base.j2 missing block {block!r}"
            )

    def test_base_renders_empty_blocks(self) -> None:
        builder = PromptBuilder(
            template_path="base.j2", templates_dir=PROMPTS_DIR
        )
        out = builder.run()["prompt"]
        assert "You are llmcode" in out

    def test_child_extending_base_renders(self) -> None:
        child_source = (
            '{% extends "base.j2" %}'
            "{% block mode_specific %}MODE-OVERRIDE{% endblock %}"
        )
        builder = PromptBuilder(
            template=child_source, templates_dir=PROMPTS_DIR
        )
        out = builder.run()["prompt"]
        assert "MODE-OVERRIDE" in out
        assert "You are llmcode" in out


class TestNonEmpty:
    def test_non_base_templates_have_content(self) -> None:
        for rel in ALL_TEMPLATES:
            if rel == "base.j2":
                continue
            content = (PROMPTS_DIR / rel).read_text().strip()
            assert content, f"migrated template {rel} is empty"
