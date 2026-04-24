"""Unit tests for the v13 prompt loader + deprecated shim.

Covers:
* :func:`load_intro_prompt` — reads ``profile.prompt_template``,
  accepts both short names and repo-style paths, falls back cleanly
  on missing templates.
* :func:`select_intro_prompt` — deprecated shim emits
  :class:`DeprecationWarning`, delegates to the profile registry via
  ``resolve_profile_for_model`` + ``load_intro_prompt``. Phase C
  deleted the hardcoded if-ladder that used to back this shim.
"""
from __future__ import annotations

import warnings

import pytest

from llm_code.runtime import profile_registry as pr
from llm_code.runtime.model_profile import ModelProfile
from llm_code.runtime.prompt import (
    _template_path_to_name,
    load_intro_prompt,
    select_intro_prompt,
)


@pytest.fixture(autouse=True)
def _reset_registry():
    """Isolate every test from the process-global registry.

    The shim lazily loads the built-in TOMLs on first call; resetting
    before/after keeps the test environment deterministic.
    """
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

    def test_empty_model_returns_default_prompt(self) -> None:
        """Empty model id falls back to the default profile — which
        has ``prompt_template=""`` — so ``load_intro_prompt`` emits
        the ``default.j2`` template."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            out = select_intro_prompt("")
        assert "coding assistant running inside a terminal" in out

    def test_shim_delegates_to_profile_registry_for_glm(self) -> None:
        """The shim must surface the GLM-tuned prompt for ``glm-5.1``
        after Phase B migrated the GLM profile to declare ``[prompt]``."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            out = select_intro_prompt("glm-5.1")
        assert "powered by GLM (Zhipu)" in out

    def test_shim_delegates_to_profile_registry_for_claude(self) -> None:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            out = select_intro_prompt("claude-sonnet-4-6")
        # anthropic.j2 contains a distinctive phrase.
        assert "powered by Claude" in out

    def test_shim_delegates_to_profile_registry_for_beast(self) -> None:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            out = select_intro_prompt("gpt-4-turbo")
        assert "# Beast" in out

    def test_shim_delegates_to_profile_registry_for_qwen(self) -> None:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            out = select_intro_prompt("qwen3.5-122b")
        # qwen.j2 contains a distinctive phrase.
        assert "powered by Qwen" in out

    def test_unknown_model_falls_back_to_default_template(self) -> None:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            out = select_intro_prompt("some-brand-new-model-99b")
        # Unknown ids resolve to the default profile which has no
        # template → load_intro_prompt emits default.j2.
        assert "coding assistant running inside a terminal" in out

    def test_user_profile_takes_precedence_over_builtins(self) -> None:
        """A pre-registered profile wins over any built-in loaded
        lazily by the shim — user overrides must be respected."""
        pr.register_profile(
            ModelProfile(
                name="Fake-FooChat",
                prompt_template="models/glm.j2",
                prompt_match=("foochat",),
            )
        )
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            out = select_intro_prompt("foochat-13b")
        # Routed through the user profile's glm template.
        assert "powered by GLM (Zhipu)" in out

    def test_profile_match_but_no_template_yields_default(self) -> None:
        """A profile that matches the id but carries no
        ``prompt_template`` yields the default prompt — Phase C
        deleted the legacy fallback ladder so empty templates can no
        longer be silently upgraded."""
        pr.register_profile(
            ModelProfile(
                name="MatchOnly",
                prompt_match=("novelmodel",),
                prompt_template="",
            )
        )
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            out = select_intro_prompt("novelmodel-v2")
        assert "coding assistant running inside a terminal" in out

    def test_shim_triggers_lazy_builtin_load(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[str] = []
        original = pr._ensure_builtin_profiles_loaded

        def _spy() -> None:
            calls.append("called")
            original()

        monkeypatch.setattr(pr, "_ensure_builtin_profiles_loaded", _spy)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            select_intro_prompt("claude-sonnet-4-6")
        assert calls == ["called"]
