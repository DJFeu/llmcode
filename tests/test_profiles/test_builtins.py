"""Tests for ``llm_code.profiles.builtins`` (v2.10.0 M1).

The helper resolves wheel-bundled profile paths via
:mod:`importlib.resources`. These tests verify:

* The bundled directory is discoverable and contains the expected
  set of profile files.
* Friendly-name lookup matches both ``"glm-5.1"`` and ``"65-glm-5.1"``.
* The bundled GLM profile parses cleanly and pins the v2.9.x flags
  the runtime relies on so a future ``rm -rf llm_code/_builtins``
  trips this test instead of silently regressing the install.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from llm_code.profiles.builtins import (
    builtin_profile_dir,
    builtin_profile_path,
    list_builtin_profile_paths,
    strip_numeric_prefix,
)


def _load_toml(path: Path) -> dict:
    if sys.version_info >= (3, 11):
        import tomllib
    else:  # pragma: no cover — exercised on 3.10 only
        import tomli as tomllib  # type: ignore[no-redef]
    with open(path, "rb") as f:
        return tomllib.load(f)


class TestBuiltinProfileDir:
    def test_returns_existing_directory(self) -> None:
        d = builtin_profile_dir()
        assert d.is_dir(), f"bundled profiles dir missing: {d}"

    def test_contains_at_least_one_toml(self) -> None:
        d = builtin_profile_dir()
        tomls = list(d.glob("*.toml"))
        assert tomls, f"no .toml profiles bundled in {d}"


class TestListBuiltinProfilePaths:
    def test_returns_sorted_paths(self) -> None:
        paths = list_builtin_profile_paths()
        assert paths, "expected bundled profiles, got none"
        names = [p.name for p in paths]
        assert names == sorted(names), (
            "list_builtin_profile_paths() must return sorted output"
        )

    def test_includes_glm_and_qwen_and_claude(self) -> None:
        names = {p.name for p in list_builtin_profile_paths()}
        # Spot-check a representative slice of the bundled profiles.
        assert "65-glm-5.1.toml" in names
        assert "45-qwen3.5-122b.toml" in names
        assert "30-claude-sonnet.toml" in names

    def test_only_returns_toml_files(self) -> None:
        paths = list_builtin_profile_paths()
        assert all(p.suffix == ".toml" for p in paths)


class TestStripNumericPrefix:
    @pytest.mark.parametrize(
        "stem,expected",
        [
            ("65-glm-5.1", "glm-5.1"),
            ("30-claude-sonnet", "claude-sonnet"),
            ("99-custom-local", "custom-local"),
            ("glm-no-prefix", "glm-no-prefix"),
            ("nodash99", "nodash99"),
            ("9-too-short-prefix", "9-too-short-prefix"),
            ("ab-not-numeric", "ab-not-numeric"),
        ],
    )
    def test_strip(self, stem: str, expected: str) -> None:
        assert strip_numeric_prefix(stem) == expected


class TestBuiltinProfilePath:
    def test_resolves_by_bare_slug(self) -> None:
        p = builtin_profile_path("glm-5.1")
        assert p is not None
        assert p.name == "65-glm-5.1.toml"

    def test_resolves_by_full_stem(self) -> None:
        p = builtin_profile_path("65-glm-5.1")
        assert p is not None
        assert p.name == "65-glm-5.1.toml"

    def test_bare_and_full_resolve_identically(self) -> None:
        bare = builtin_profile_path("glm-5.1")
        full = builtin_profile_path("65-glm-5.1")
        assert bare == full

    def test_case_insensitive(self) -> None:
        assert builtin_profile_path("GLM-5.1") is not None
        assert builtin_profile_path("Glm-5.1") is not None

    def test_accepts_filename_with_extension(self) -> None:
        p = builtin_profile_path("glm-5.1.toml")
        assert p is not None
        assert p.name == "65-glm-5.1.toml"

    def test_returns_none_for_unknown(self) -> None:
        assert builtin_profile_path("does-not-exist") is None

    def test_returns_none_for_empty_input(self) -> None:
        assert builtin_profile_path("") is None


class TestBundledGlmProfileShape:
    """The bundled GLM profile must keep the v2.9.x runtime flags.

    These pin the values that v2.9.0 P1 / P2 + v2.9.1 floor + v2.9.2
    runtime clamp depend on. If any of them regress, the wheel ships
    a profile that contradicts the runtime defaults — exactly the
    packaging gap v2.10.0 is closing.
    """

    @pytest.fixture(scope="class")
    def glm(self) -> dict:
        path = builtin_profile_path("glm-5.1")
        assert path is not None, "GLM-5.1 profile not bundled in wheel"
        return _load_toml(path)

    def test_parses_cleanly(self, glm: dict) -> None:
        assert isinstance(glm, dict)
        assert glm.get("name"), "GLM profile missing name"

    def test_p1_parallel_tools_enabled(self, glm: dict) -> None:
        assert glm.get("parallel_tools", {}).get("enable_parallel_tools") is True

    def test_p2_compress_old_tool_results_enabled(self, glm: dict) -> None:
        tc = glm.get("tool_consumption", {})
        assert tc.get("compress_old_tool_results") is True

    def test_p3_compile_after_tool_calls_threshold(self, glm: dict) -> None:
        tc = glm.get("tool_consumption", {})
        assert tc.get("compile_after_tool_calls") == 3

    def test_v291_compile_thinking_budget_floor(self, glm: dict) -> None:
        tc = glm.get("tool_consumption", {})
        budget = tc.get("compile_thinking_budget")
        assert isinstance(budget, int) and budget >= 512, (
            "compile_thinking_budget must stay at the v2.9.1 floor (>= 512); "
            f"got {budget!r}"
        )

    def test_force_xml_tools(self, glm: dict) -> None:
        # The runtime relies on this for the GLM dispatch path.
        assert glm.get("provider", {}).get("force_xml_tools") is True


class TestRegistryStillResolvesGlm:
    """End-to-end: the runtime profile registry sees the GLM profile.

    Confirms M1 didn't only ship the file but actually replaces the
    legacy ``examples/`` lookup. ``_load_builtin_profiles`` now walks
    the wheel-bundled directory; resolving ``"glm-5.1"`` should hit it.
    """

    def test_glm_profile_resolved_by_runtime_registry(self) -> None:
        from llm_code.runtime import profile_registry as pr

        pr._reset_registry_for_tests()
        pr._ensure_builtin_profiles_loaded()
        try:
            profile = pr.resolve_profile_for_model("glm-5.1")
            assert profile is not pr._DEFAULT_PROFILE, (
                "runtime registry returned the default profile for glm-5.1; "
                "expected the bundled GLM profile to be loaded"
            )
            # Spot-check one of the v2.9.x flags so we know the bundled
            # file (not a stale on-disk override) is what got loaded.
            assert profile.enable_parallel_tools is True
            assert profile.compress_old_tool_results is True
            assert profile.compile_after_tool_calls == 3
            assert profile.compile_thinking_budget >= 512
        finally:
            pr._reset_registry_for_tests()
