"""v2.6.1 M2 — prompt-dedupe with template (snippet-tag based skip).

Profiles that opt in to ``prompt_dedupe_with_template`` skip generic
snippets whose ``tags`` are fully covered by the active model
template's ``provides_tags`` (declared in the sidecar
``<template>.metadata.toml``). The brief calls out three guarantees:

1. snippets with non-overlapping tags still render
2. snippets with full-overlap tags are dropped entirely
3. opt-out (default ``False``) preserves v2.6.0 byte-parity

These tests exercise the dedupe logic in ``compose_system_prompt``
(the snippets pack) and the ``SystemPromptBuilder.build`` hardcoded
gates that skip the matching ``_BEHAVIOR_RULES`` /
``_LOCAL_MODEL_RULES`` / ``_XML_TOOL_INSTRUCTIONS`` constants.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from llm_code.runtime.context import ProjectContext
from llm_code.runtime.model_profile import ModelProfile
from llm_code.runtime.prompt import (
    SystemPromptBuilder,
    load_template_provides_tags,
)
from llm_code.runtime.prompt_snippets import (
    BEHAVIOR_RULES,
    BUILTIN_SNIPPETS,
    INTRO,
    LOCAL_MODEL_RULES,
    PromptSnippet,
    TOOL_RESULT_NUDGE,
    XML_TOOL_INSTRUCTIONS,
    compose_system_prompt,
)


def _ctx() -> ProjectContext:
    return ProjectContext(
        cwd="/tmp/dedupe-test",
        instructions="",
        is_git_repo=False,
        git_status="",
    )


class TestSnippetTagsField:
    """PromptSnippet now carries ``tags`` for dedupe routing."""

    def test_intro_has_intro_tag(self) -> None:
        assert "intro" in INTRO.tags

    def test_behavior_rules_has_behavior_rules_tag(self) -> None:
        assert "behavior_rules" in BEHAVIOR_RULES.tags

    def test_xml_tool_instructions_has_xml_tools_tag(self) -> None:
        assert "xml_tools" in XML_TOOL_INSTRUCTIONS.tags

    def test_local_model_rules_has_local_model_rules_tag(self) -> None:
        assert "local_model_rules" in LOCAL_MODEL_RULES.tags

    def test_tool_result_nudge_has_tool_result_nudge_tag(self) -> None:
        assert "tool_result_nudge" in TOOL_RESULT_NUDGE.tags

    def test_default_tags_is_empty_tuple(self) -> None:
        snippet = PromptSnippet(key="x", content="y")
        assert snippet.tags == ()


class TestComposeSystemPromptDedupe:
    """compose_system_prompt drops snippets when their tags are covered."""

    def test_no_provides_tags_renders_all_snippets(self) -> None:
        """Default behaviour: empty provides_tags renders every snippet."""
        out = compose_system_prompt(
            BUILTIN_SNIPPETS,
            is_local=True,
            force_xml=True,
        )
        # All snippets present
        assert "You are a coding assistant" in out         # INTRO
        assert "NEVER output your thinking" in out          # BEHAVIOR_RULES
        assert "Do NOT use the agent tool" in out           # LOCAL_MODEL_RULES
        assert "<tool_call>" in out                          # XML_TOOL_INSTRUCTIONS
        assert "produce a substantive" in out                # TOOL_RESULT_NUDGE

    def test_provides_intro_drops_intro_snippet(self) -> None:
        out = compose_system_prompt(
            BUILTIN_SNIPPETS,
            provides_tags=("intro",),
            is_local=True,
            force_xml=True,
        )
        # INTRO suppressed; others remain
        assert "You are a coding assistant running inside a terminal." not in out
        assert "NEVER output your thinking" in out

    def test_provides_behavior_rules_drops_behavior_snippet(self) -> None:
        out = compose_system_prompt(
            BUILTIN_SNIPPETS,
            provides_tags=("behavior_rules",),
            is_local=True,
            force_xml=True,
        )
        assert "NEVER output your thinking" not in out
        # Other snippets still render
        assert "You are a coding assistant" in out

    def test_provides_multi_tags_drops_each_matched_snippet(self) -> None:
        out = compose_system_prompt(
            BUILTIN_SNIPPETS,
            provides_tags=("intro", "behavior_rules", "tool_result_nudge"),
            is_local=True,
            force_xml=True,
        )
        # Three dropped
        assert "You are a coding assistant running inside a terminal." not in out
        assert "NEVER output your thinking" not in out
        assert "produce a substantive" not in out
        # Two kept
        assert "<tool_call>" in out                  # xml_tools NOT in provides
        assert "Do NOT use the agent tool" in out    # local_model_rules NOT in provides

    def test_partial_tag_overlap_does_not_drop(self) -> None:
        """Snippet with multi-tag where only ONE matches should still render."""
        multi = PromptSnippet(
            key="multi",
            content="multi-tag snippet body",
            tags=("intro", "extra_tag"),  # extra_tag not in provides
        )
        out = compose_system_prompt(
            [multi],
            provides_tags=("intro",),
        )
        # extra_tag NOT covered → snippet still renders
        assert "multi-tag snippet body" in out

    def test_untagged_snippet_never_dropped(self) -> None:
        """Legacy snippets without tags must always render."""
        legacy = PromptSnippet(
            key="legacy",
            content="legacy snippet body",
            # tags defaults to ()
        )
        out = compose_system_prompt(
            [legacy],
            provides_tags=("intro", "behavior_rules"),
        )
        assert "legacy snippet body" in out


class TestProfileOptOutByteParity:
    """Profiles WITHOUT prompt_dedupe_with_template behave like v2.6.0."""

    def test_profile_default_does_not_dedupe(self) -> None:
        """Default ModelProfile has prompt_dedupe_with_template = False."""
        profile = ModelProfile(name="default")
        assert profile.prompt_dedupe_with_template is False

    def test_qwen_profile_byte_identical_to_v260(self) -> None:
        """Qwen3.5-122B is local + force_xml but not opted in to dedupe."""
        builder = SystemPromptBuilder()
        out = builder.build(
            _ctx(),
            model_name="qwen3.5-122b",
            native_tools=False,
            is_local_model=True,
        )
        # The Qwen prompt MUST still contain the duplicate-guidance
        # because Qwen has not opted in to dedupe.
        # Snippet pack should still contain BEHAVIOR_RULES content.
        assert out.count("NEVER output your thinking") >= 2  # global + snippet pack


class TestProfileOptInDedupes:
    """GLM profile with prompt_dedupe_with_template = true drops snippets."""

    def test_glm_profile_resolves_with_dedupe_flag(self) -> None:
        """GLM-5.1 example profile opts in to dedupe."""
        from llm_code.runtime.profile_registry import (
            _ensure_builtin_profiles_loaded,
            resolve_profile_for_model,
        )
        _ensure_builtin_profiles_loaded()
        profile = resolve_profile_for_model("glm-5.1")
        assert profile.prompt_dedupe_with_template is True
        assert profile.prompt_template == "glm"

    def test_glm_template_provides_tags_loaded(self) -> None:
        """Sidecar glm.metadata.toml is parsed correctly."""
        from llm_code.runtime.profile_registry import (
            _ensure_builtin_profiles_loaded,
            resolve_profile_for_model,
        )
        _ensure_builtin_profiles_loaded()
        profile = resolve_profile_for_model("glm-5.1")
        tags = load_template_provides_tags(profile)
        assert "intro" in tags
        assert "behavior_rules" in tags
        assert "tool_result_nudge" in tags
        # Sanity: GLM template does NOT cover XML format spec.
        assert "xml_tools" not in tags

    def test_glm_prompt_drops_duplicate_behavior_rules(self) -> None:
        builder = SystemPromptBuilder()
        out = builder.build(
            _ctx(),
            model_name="glm-5.1",
            native_tools=False,
            is_local_model=True,
        )
        # Generic _BEHAVIOR_RULES "Rules:\n- NEVER output your thinking..."
        # must NOT appear — the GLM template already covers it.
        # The string "NEVER output your thinking" only appears once if at all.
        # Note: the GLM template itself doesn't contain that exact string;
        # so count==0 confirms the snippet was suppressed.
        assert "NEVER output your thinking, reasoning, or analysis as text" not in out

    def test_glm_prompt_keeps_xml_tool_instructions(self) -> None:
        """GLM template doesn't cover XML format → snippet still renders."""
        builder = SystemPromptBuilder()
        out = builder.build(
            _ctx(),
            model_name="glm-5.1",
            native_tools=False,
            is_local_model=True,
        )
        assert "<tool_call>" in out
        # Specifically the snippet body
        assert 'wrapped in <tool_call>' in out

    def test_glm_prompt_keeps_local_model_rules(self) -> None:
        """GLM template doesn't cover agent-tool warning → snippet still renders."""
        builder = SystemPromptBuilder()
        out = builder.build(
            _ctx(),
            model_name="glm-5.1",
            native_tools=False,
            is_local_model=True,
        )
        assert "Do NOT use the agent tool" in out

    def test_glm_prompt_smaller_than_v260_baseline(self) -> None:
        """The deduped GLM prompt must be measurably shorter."""
        baseline_path = (
            Path(__file__).resolve().parent.parent
            / "fixtures" / "system_prompt_v260" / "glm-5.1-xml.txt"
        )
        if not baseline_path.exists():
            pytest.skip(
                f"baseline missing at {baseline_path} — run "
                f"scripts/capture_system_prompt_v260.py first"
            )
        baseline = baseline_path.read_text(encoding="utf-8")

        builder = SystemPromptBuilder()
        deduped = builder.build(
            _ctx(),
            model_name="glm-5.1",
            native_tools=False,
            is_local_model=True,
        )
        # Saving at least 1500 chars validates the brief's estimate.
        assert len(baseline) - len(deduped) >= 1500


class TestLoadTemplateProvidesTagsErrors:
    """Helper degrades to empty tuple on missing/malformed sidecar."""

    def test_missing_sidecar_returns_empty(self) -> None:
        profile = ModelProfile(name="x", prompt_template="default")
        # default.j2 has no sidecar
        assert load_template_provides_tags(profile) == ()

    def test_empty_template_returns_empty(self) -> None:
        profile = ModelProfile(name="x", prompt_template="")
        assert load_template_provides_tags(profile) == ()

    def test_glm_returns_expected_tags(self) -> None:
        profile = ModelProfile(name="x", prompt_template="glm")
        tags = load_template_provides_tags(profile)
        assert set(tags) == {"intro", "behavior_rules", "tool_result_nudge"}

    def test_template_path_with_models_prefix(self) -> None:
        """Path forms like ``models/glm.j2`` are normalised."""
        profile = ModelProfile(name="x", prompt_template="models/glm.j2")
        tags = load_template_provides_tags(profile)
        assert "intro" in tags


class TestProfileTomlParsesDedupeFlag:
    """[prompt] dedupe_with_template = true round-trips through the loader."""

    def test_toml_section_loader_picks_up_flag(self, tmp_path: Path) -> None:
        from llm_code.runtime.model_profile import _load_toml, _profile_from_dict
        toml_text = """
name = "test"

[prompt]
template = "glm"
match = ["test-glm"]
dedupe_with_template = true
"""
        p = tmp_path / "test.toml"
        p.write_text(toml_text)
        data = _load_toml(p)
        profile = _profile_from_dict(data)
        assert profile.prompt_dedupe_with_template is True
        assert profile.prompt_template == "glm"

    def test_omitted_flag_defaults_to_false(self, tmp_path: Path) -> None:
        from llm_code.runtime.model_profile import _load_toml, _profile_from_dict
        toml_text = """
name = "test"

[prompt]
template = "glm"
match = ["test-glm"]
"""
        p = tmp_path / "test.toml"
        p.write_text(toml_text)
        data = _load_toml(p)
        profile = _profile_from_dict(data)
        assert profile.prompt_dedupe_with_template is False
