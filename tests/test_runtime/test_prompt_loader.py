"""Unit tests for the v13 Phase A prompt loader + deprecated shim.

Covers:
* :func:`load_intro_prompt` — reads ``profile.prompt_template``,
  accepts both short names and repo-style paths, falls back cleanly
  on missing templates.
* :func:`select_intro_prompt` — deprecated shim emits
  :class:`DeprecationWarning`, delegates to the profile registry
  when a migrated profile matches, falls back to the historical
  ladder otherwise so Phase A preserves byte-level output.
"""
from __future__ import annotations

import warnings

import pytest

from llm_code.runtime import profile_registry as pr
from llm_code.runtime.model_profile import ModelProfile
from llm_code.runtime.prompt import (
    _legacy_select_intro_prompt,
    _template_path_to_name,
    load_intro_prompt,
    select_intro_prompt,
)


@pytest.fixture(autouse=True)
def _reset_registry():
    """Isolate every test from the process-global registry."""
    pr._reset_registry_for_tests()
    yield
    pr._reset_registry_for_tests()


# ── load_intro_prompt ────────────────────────────────────────────────


class TestLoadIntroPrompt:
    def test_reads_configured_template_by_short_name(self) -> None:
        # ``glm.j2`` ships in the source tree and has a unique marker.
        profile = ModelProfile(prompt_template="glm")
        out = load_intro_prompt(profile)
        assert "powered by GLM (Zhipu)" in out

    def test_reads_configured_template_by_full_path(self) -> None:
        profile = ModelProfile(prompt_template="models/glm.j2")
        out = load_intro_prompt(profile)
        assert "powered by GLM (Zhipu)" in out

    def test_reads_configured_template_with_partial_path(self) -> None:
        # Accepts ``glm.j2`` (no ``models/`` prefix) as well.
        profile = ModelProfile(prompt_template="glm.j2")
        out = load_intro_prompt(profile)
        assert "powered by GLM (Zhipu)" in out

    def test_empty_template_falls_back_to_default_prompt(self) -> None:
        profile = ModelProfile(prompt_template="")
        out = load_intro_prompt(profile)
        # ``default.j2`` opens with the generic intro.
        assert "coding assistant running inside a terminal" in out

    def test_missing_template_file_returns_inline_fallback(self) -> None:
        # ``_read_prompt`` never raises — it returns an inline safe
        # default when the configured file is absent.
        profile = ModelProfile(prompt_template="models/does-not-exist.j2")
        out = load_intro_prompt(profile)
        assert "coding assistant" in out
        assert out  # non-empty

    def test_anthropic_template_loaded(self) -> None:
        profile = ModelProfile(prompt_template="anthropic")
        out = load_intro_prompt(profile)
        # Sanity — anthropic template is distinct from default.
        assert out != load_intro_prompt(ModelProfile(prompt_template="default"))

    def test_beast_template_loaded(self) -> None:
        profile = ModelProfile(prompt_template="beast")
        out = load_intro_prompt(profile)
        assert "# Beast" in out


class TestTemplatePathToName:
    def test_short_name_unchanged(self) -> None:
        assert _template_path_to_name("glm") == "glm"

    def test_strips_models_prefix(self) -> None:
        assert _template_path_to_name("models/glm") == "glm"

    def test_strips_j2_suffix(self) -> None:
        assert _template_path_to_name("glm.j2") == "glm"

    def test_strips_both_prefix_and_suffix(self) -> None:
        assert _template_path_to_name("models/glm.j2") == "glm"

    def test_empty_input_yields_default(self) -> None:
        assert _template_path_to_name("") == "default"


# ── select_intro_prompt shim ─────────────────────────────────────────


class TestSelectIntroPromptShim:
    def test_emits_deprecation_warning(self) -> None:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            select_intro_prompt("claude-sonnet-4-6")
        dep = [w for w in caught if issubclass(w.category, DeprecationWarning)]
        assert len(dep) == 1
        assert "select_intro_prompt" in str(dep[0].message)
        assert "deprecated" in str(dep[0].message)

    def test_emits_warning_on_every_call(self) -> None:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            select_intro_prompt("glm-5.1")
            select_intro_prompt("gpt-4o")
            select_intro_prompt("claude-opus-4-6")
        dep = [w for w in caught if issubclass(w.category, DeprecationWarning)]
        assert len(dep) == 3

    def test_empty_model_delegates_to_legacy_default(self) -> None:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            shim_out = select_intro_prompt("")
            legacy_out = _legacy_select_intro_prompt("")
        assert shim_out == legacy_out

    def test_unmigrated_model_matches_legacy_output_claude(self) -> None:
        # In Phase A no built-in TOML has a ``[prompt]`` section — the
        # registry returns the default profile → shim falls back to
        # the historical ladder. The output must match byte-for-byte.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            shim_out = select_intro_prompt("claude-sonnet-4-6")
            legacy_out = _legacy_select_intro_prompt("claude-sonnet-4-6")
        assert shim_out == legacy_out

    def test_unmigrated_model_matches_legacy_output_gpt4(self) -> None:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            shim_out = select_intro_prompt("gpt-4-turbo")
            legacy_out = _legacy_select_intro_prompt("gpt-4-turbo")
        assert shim_out == legacy_out

    def test_unmigrated_model_matches_legacy_output_qwen(self) -> None:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            shim_out = select_intro_prompt("qwen3.5-122b")
            legacy_out = _legacy_select_intro_prompt("qwen3.5-122b")
        assert shim_out == legacy_out

    def test_unmigrated_model_matches_legacy_output_glm(self) -> None:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            shim_out = select_intro_prompt("glm-5.1")
            legacy_out = _legacy_select_intro_prompt("glm-5.1")
        assert shim_out == legacy_out

    def test_unmigrated_model_matches_legacy_unknown(self) -> None:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            shim_out = select_intro_prompt("some-brand-new-model-99b")
            legacy_out = _legacy_select_intro_prompt("some-brand-new-model-99b")
        assert shim_out == legacy_out

    def test_migrated_profile_takes_precedence(self) -> None:
        # Pre-seed the registry with a migrated profile. The shim must
        # route through ``load_intro_prompt`` for this model — the
        # legacy ladder would have picked ``default`` (no known token),
        # but the profile overrides that.
        pr.register_profile(ModelProfile(
            name="Fake-FooChat",
            prompt_template="models/glm.j2",
            prompt_match=("foochat",),
        ))
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            shim_out = select_intro_prompt("foochat-13b")
            legacy_out = _legacy_select_intro_prompt("foochat-13b")
        # Profile-driven path picked the glm template; legacy picked default.
        assert "powered by GLM (Zhipu)" in shim_out
        assert shim_out != legacy_out

    def test_profile_match_but_no_template_falls_back_to_legacy(self) -> None:
        # Profile matches the id but carries no ``prompt_template`` —
        # the shim treats this as "nothing migrated" and falls through
        # to the legacy ladder so existing behaviour is preserved.
        pr.register_profile(ModelProfile(
            name="MatchOnly",
            prompt_match=("claude",),
            prompt_template="",
        ))
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            shim_out = select_intro_prompt("claude-sonnet-4-6")
            legacy_out = _legacy_select_intro_prompt("claude-sonnet-4-6")
        assert shim_out == legacy_out

    def test_shim_triggers_lazy_builtin_load(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[str] = []
        original = pr._ensure_builtin_profiles_loaded

        def _spy() -> None:
            calls.append("called")
            original()

        monkeypatch.setattr(
            pr, "_ensure_builtin_profiles_loaded", _spy
        )
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            select_intro_prompt("claude-sonnet-4-6")
        assert calls == ["called"]
