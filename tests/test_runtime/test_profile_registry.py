"""Unit tests for the v13 Phase A profile registry.

Covers:
* ``register_profile`` — append, collision detection, opt-out flag
* ``resolve_profile_for_model`` — substring match, first-match-wins,
  case-insensitive on model id, empty/unknown fallback to default
* ``_load_builtin_profiles`` — directory sweep, malformed TOML
  swallowed, missing dir safe
* ``_ensure_builtin_profiles_loaded`` — idempotent guard
* ``_reset_registry_for_tests`` — full clear

The registry is process-global so every test explicitly resets it via
the ``registry`` fixture, which also yields helpers for building
throw-away profiles without repeating boilerplate.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from llm_code.runtime.model_profile import ModelProfile
from llm_code.runtime import profile_registry as pr


@pytest.fixture(autouse=True)
def _reset_registry():
    """Reset the global registry before and after every test."""
    pr._reset_registry_for_tests()
    yield
    pr._reset_registry_for_tests()


def _make(name: str, match: tuple[str, ...] = (), template: str = "") -> ModelProfile:
    """Build a throw-away profile with just the v13 Phase A fields set."""
    return ModelProfile(
        name=name,
        prompt_match=match,
        prompt_template=template,
    )


# ── Resolution ────────────────────────────────────────────────────────


class TestResolveProfile:
    def test_returns_default_for_empty_id(self) -> None:
        assert pr.resolve_profile_for_model("") is pr._DEFAULT_PROFILE

    def test_returns_default_for_unknown_id_when_registry_empty(self) -> None:
        assert pr.resolve_profile_for_model("anything") is pr._DEFAULT_PROFILE

    def test_returns_default_for_unknown_id_with_profiles(self) -> None:
        pr.register_profile(_make("GLM", match=("glm",)))
        assert pr.resolve_profile_for_model("claude-sonnet") is pr._DEFAULT_PROFILE

    def test_picks_first_matching_profile(self) -> None:
        glm = _make("GLM", match=("glm",))
        pr.register_profile(glm)
        assert pr.resolve_profile_for_model("glm-5.1") is glm

    def test_substring_match_anywhere_in_id(self) -> None:
        p = _make("Qwen", match=("qwen",))
        pr.register_profile(p)
        # Token can live anywhere in the model id — prefix, middle, suffix.
        assert pr.resolve_profile_for_model("qwen3.5-plus") is p
        assert pr.resolve_profile_for_model("org/qwen-coder") is p
        assert pr.resolve_profile_for_model("some-qwen") is p

    def test_is_case_insensitive_on_model_id(self) -> None:
        p = _make("GLM", match=("glm",))
        pr.register_profile(p)
        assert pr.resolve_profile_for_model("GLM-5.1") is p
        assert pr.resolve_profile_for_model("Glm-5.1") is p
        assert pr.resolve_profile_for_model("ZHIPU-GLM") is p

    def test_first_match_wins_when_multiple_profiles_claim_model(self) -> None:
        # Two profiles with disjoint tokens — whichever is registered
        # first and matches first in iteration wins.
        first = _make("First", match=("foo",))
        second = _make("Second", match=("bar",))
        pr.register_profile(first)
        pr.register_profile(second)
        # The id contains both tokens — first registered profile wins.
        assert pr.resolve_profile_for_model("foo-bar-model") is first

    def test_empty_token_never_matches(self) -> None:
        # An empty-string token would otherwise match every non-empty
        # id via the ``"" in m`` substring rule; the resolver guards
        # against this so authoring mistakes stay silent.
        p = _make("Broken", match=("",))
        pr.register_profile(p)
        assert pr.resolve_profile_for_model("anything") is pr._DEFAULT_PROFILE

    def test_default_profile_has_no_prompt_template(self) -> None:
        # The default instance returns an un-configured profile so the
        # shim can detect "nothing matched → fall back to legacy".
        assert pr._DEFAULT_PROFILE.prompt_template == ""
        assert pr._DEFAULT_PROFILE.prompt_match == ()

    def test_user_profile_order_wins_over_later_registrations(self) -> None:
        # Mirrors the "user profiles first, built-ins after" contract:
        # whoever registers first shadows later entries on shared tokens.
        user = _make("User", match=("glm",), template="models/user.j2")
        builtin = _make("Builtin", match=("glm",))
        pr.register_profile(user)
        pr.register_profile(builtin, check_collision=False)
        assert pr.resolve_profile_for_model("glm-5.1") is user


# ── Registration + collisions ────────────────────────────────────────


class TestRegisterProfile:
    def test_appends_to_internal_list(self) -> None:
        p = _make("A", match=("a",))
        pr.register_profile(p)
        assert pr._PROFILES[-1] is p
        assert len(pr._PROFILES) == 1

    def test_multiple_profiles_coexist(self) -> None:
        pr.register_profile(_make("A", match=("alpha",)))
        pr.register_profile(_make("B", match=("beta",)))
        pr.register_profile(_make("C", match=("gamma",)))
        assert len(pr._PROFILES) == 3

    def test_raises_on_duplicate_match_token(self) -> None:
        pr.register_profile(_make("First", match=("glm",)))
        with pytest.raises(pr.ProfileMatchCollision) as exc:
            pr.register_profile(_make("Second", match=("glm",)))
        # Error message names the colliding token + both profiles.
        assert "glm" in str(exc.value)
        assert "First" in str(exc.value)
        assert "Second" in str(exc.value)

    def test_raises_when_only_one_of_many_tokens_collides(self) -> None:
        pr.register_profile(_make("First", match=("qwen", "tongyi")))
        with pytest.raises(pr.ProfileMatchCollision):
            # "qwen" clashes; "llama" is fresh — but the partial clash
            # is still fatal.
            pr.register_profile(_make("Second", match=("llama", "qwen")))

    def test_allows_duplicate_when_check_collision_disabled(self) -> None:
        pr.register_profile(_make("First", match=("glm",)))
        # Explicit opt-out — used by bulk loaders / dev reloads.
        pr.register_profile(_make("Second", match=("glm",)), check_collision=False)
        assert len(pr._PROFILES) == 2

    def test_empty_match_never_collides(self) -> None:
        # Profiles without a ``prompt_match`` tuple (e.g. the Phase A
        # baseline TOMLs that have no ``[prompt]`` section) can be
        # registered any number of times without tripping the guard.
        pr.register_profile(_make("A"))
        pr.register_profile(_make("B"))
        pr.register_profile(_make("C"))
        assert len(pr._PROFILES) == 3

    def test_collision_class_is_runtime_error_subclass(self) -> None:
        assert issubclass(pr.ProfileMatchCollision, RuntimeError)

    def test_rolled_back_registry_does_not_keep_colliding_profile(self) -> None:
        pr.register_profile(_make("First", match=("glm",)))
        with pytest.raises(pr.ProfileMatchCollision):
            pr.register_profile(_make("Second", match=("glm",)))
        # The collision guard raises before ``append`` runs — second
        # profile must not be in the registry.
        names = [p.name for p in pr._PROFILES]
        assert names == ["First"]


# ── Bulk loader: _load_builtin_profiles ──────────────────────────────


class TestLoadBuiltinProfiles:
    def test_returns_zero_when_dir_does_not_exist(self, tmp_path: Path) -> None:
        missing = tmp_path / "nowhere"
        count = pr._load_builtin_profiles(missing)
        assert count == 0
        assert len(pr._PROFILES) == 0

    def test_loads_every_valid_toml(self, tmp_path: Path) -> None:
        (tmp_path / "a.toml").write_text('name = "Alpha"\n')
        (tmp_path / "b.toml").write_text('name = "Beta"\n')
        count = pr._load_builtin_profiles(tmp_path)
        assert count == 2
        names = sorted(p.name for p in pr._PROFILES)
        assert names == ["Alpha", "Beta"]

    def test_ignores_non_toml_files(self, tmp_path: Path) -> None:
        (tmp_path / "a.toml").write_text('name = "Alpha"\n')
        (tmp_path / "note.txt").write_text("not a profile\n")
        count = pr._load_builtin_profiles(tmp_path)
        assert count == 1

    def test_swallows_malformed_toml(self, tmp_path: Path) -> None:
        (tmp_path / "good.toml").write_text('name = "Good"\n')
        (tmp_path / "bad.toml").write_text("this is :: not [[[ valid toml")
        # Should not raise; the good file still loads.
        count = pr._load_builtin_profiles(tmp_path)
        assert count == 1
        assert pr._PROFILES[0].name == "Good"

    def test_swallows_collision_errors(self, tmp_path: Path) -> None:
        (tmp_path / "a.toml").write_text(textwrap.dedent("""\
            name = "A"
            [prompt]
            match = ["glm"]
            template = "models/glm.j2"
        """))
        (tmp_path / "b.toml").write_text(textwrap.dedent("""\
            name = "B"
            [prompt]
            match = ["glm"]
            template = "models/other.j2"
        """))
        # Second file collides but the sweep swallows it silently.
        count = pr._load_builtin_profiles(tmp_path)
        assert count == 1
        assert pr._PROFILES[0].name == "A"

    def test_files_loaded_in_sorted_order(self, tmp_path: Path) -> None:
        # Deterministic ordering matters for "first match wins" —
        # filenames alphabetise so behaviour is reproducible.
        (tmp_path / "z-last.toml").write_text('name = "Zeta"\n')
        (tmp_path / "a-first.toml").write_text('name = "Alpha"\n')
        pr._load_builtin_profiles(tmp_path)
        assert [p.name for p in pr._PROFILES] == ["Alpha", "Zeta"]

    def test_parses_prompt_section_from_toml(self, tmp_path: Path) -> None:
        (tmp_path / "glm.toml").write_text(textwrap.dedent("""\
            name = "GLM"
            [prompt]
            template = "models/glm.j2"
            match = ["glm", "zhipu"]
        """))
        pr._load_builtin_profiles(tmp_path)
        assert pr._PROFILES[0].prompt_template == "models/glm.j2"
        assert pr._PROFILES[0].prompt_match == ("glm", "zhipu")


# ── Lazy loader: _ensure_builtin_profiles_loaded ─────────────────────


class TestEnsureBuiltinProfilesLoaded:
    def test_runs_at_most_once(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # v2.10.0 — ``_ensure_builtin_profiles_loaded`` may invoke the
        # underlying loader against up to two directories per ensure call
        # (the wheel-bundled ``llm_code/_builtins/profiles`` AND, in a
        # source checkout, the legacy ``examples/model_profiles`` fallback).
        # The contract this test pins is "at most one ensure-pass per
        # process": collect the unique directories touched, and assert
        # that subsequent ``ensure`` calls re-touch nothing.
        calls: list[Path] = []

        def _counting_loader(path: Path) -> int:
            calls.append(path)
            return 0

        monkeypatch.setattr(pr, "_load_builtin_profiles", _counting_loader)
        pr._ensure_builtin_profiles_loaded()
        first_pass = list(calls)
        pr._ensure_builtin_profiles_loaded()
        pr._ensure_builtin_profiles_loaded()
        # Subsequent ensure calls must be no-ops.
        assert calls == first_pass

    def test_reset_allows_reload(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # See note in ``test_runs_at_most_once`` about the multi-directory
        # contract. Resetting the registry must reset the guard so a
        # subsequent ensure pass re-runs the same loader sequence.
        calls: list[Path] = []

        def _counting_loader(path: Path) -> int:
            calls.append(path)
            return 0

        monkeypatch.setattr(pr, "_load_builtin_profiles", _counting_loader)
        pr._ensure_builtin_profiles_loaded()
        first_pass_len = len(calls)
        assert first_pass_len >= 1
        pr._reset_registry_for_tests()
        pr._ensure_builtin_profiles_loaded()
        # The second ensure pass should add another full set of loader
        # calls (one per discoverable directory).
        assert len(calls) == 2 * first_pass_len

    def test_real_examples_dir_loads_without_error(self) -> None:
        # Smoke test against the actual repo ``examples/model_profiles``.
        # Every shipped file must parse without raising.
        pr._ensure_builtin_profiles_loaded()
        # Phase A TOMLs have no ``[prompt]`` section → empty match tuples
        # → unknown model ids still fall through to the default.
        assert pr.resolve_profile_for_model(
            "truly-unknown-model-xyzzy"
        ) is pr._DEFAULT_PROFILE


# ── Test helpers ──────────────────────────────────────────────────────


class TestResetRegistryForTests:
    def test_clears_profiles(self) -> None:
        pr.register_profile(_make("A", match=("a",)))
        assert len(pr._PROFILES) == 1
        pr._reset_registry_for_tests()
        assert pr._PROFILES == []

    def test_clears_builtin_loaded_flag(self) -> None:
        pr._builtin_loaded = True
        pr._reset_registry_for_tests()
        assert pr._builtin_loaded is False
