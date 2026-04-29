"""v2.12.1 hotfix — profile loader inheritance from bundled built-ins.

Pre-v2.12.1 bug: ``ProfileRegistry._load_user_profiles`` merged user
TOML over ``self._profiles.get(key, _DEFAULT_PROFILE)``. The hardcoded
``_BUILTIN_PROFILES`` does NOT include ``glm-5.1`` (or any of the
TOML-only profiles bundled under ``llm_code/_builtins/profiles/``), so
a user with a v2.9-era local copy of ``glm-5.1.toml`` would merge that
copy over the empty ``_DEFAULT_PROFILE`` and silently drop every field
the bundled profile picked up in subsequent releases:

* v2.9.0 ``compress_old_tool_results``, ``enable_parallel_tools``,
  ``compile_after_tool_calls``, ``compile_thinking_budget``
* v2.11.0 ``empty_compile_retry``
* v2.12.0 ``malformed_tool_retry``

Real reproducer: a GLM-5.1 user upgraded ``llmcode-cli==2.12.0`` and
ran the news-search prompt. The v2.12 retry never fired because the
user's stale local profile lacked ``malformed_tool_retry`` so the very
first gate evaluated False.

Fix (this file's regression coverage): when the registry has no
hardcoded entry for the user's profile key, fall back to loading the
wheel-bundled built-in (``llm_code._builtins.profiles/<key>.toml``)
as the merge base BEFORE applying the user's TOML. The result: a
stale user profile picks up every new field added in subsequent
releases automatically — no manual ``llmcode profiles update``
required.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from llm_code.runtime.model_profile import (
    ProfileRegistry,
    _DEFAULT_PROFILE,
    _load_bundled_profile_base,
)


# ── Helper ───────────────────────────────────────────────────────────


def _stale_glm_toml_text() -> str:
    """Approximate what a v2.9-era ``glm-5.1.toml`` would look like —
    has the basic provider / streaming / thinking config but lacks
    every ``[tool_consumption]`` field added in v2.11+ and the
    ``[parallel_tools]`` section added in v2.9.0."""
    return """
name = "GLM-5.1 (stale v2.9-era copy)"

[provider]
type = "openai-compat"
native_tools = false
supports_reasoning = true
force_xml_tools = true

[streaming]
implicit_thinking = false
reasoning_field = "reasoning_content"

[thinking]
default_thinking_budget = 16384
post_tool_thinking_budget = 1024

[sampling]
default_temperature = 0.6
"""


# ── Bundled-profile lookup helper (v2.12.1 internal) ────────────────


class TestLoadBundledProfileBase:
    """``_load_bundled_profile_base(key)`` resolves wheel-bundled
    profile TOML files into hydrated ModelProfile objects."""

    def test_glm_returns_profile_with_v212_fields(self) -> None:
        profile = _load_bundled_profile_base("glm-5.1")
        assert profile is not None
        # v2.9.0 fields
        assert profile.compress_old_tool_results is True
        assert profile.enable_parallel_tools is True
        assert profile.compile_after_tool_calls == 3
        assert profile.compile_thinking_budget >= 512
        # v2.11.0 / v2.12.0 fields
        assert profile.empty_compile_retry is True
        assert profile.malformed_tool_retry is True

    def test_unknown_key_returns_none(self) -> None:
        """A genuinely custom name (no bundled profile) falls
        through and the caller uses ``_DEFAULT_PROFILE``."""
        assert _load_bundled_profile_base("zzz-nonexistent-xyz") is None

    def test_numeric_prefix_form_resolves(self) -> None:
        """``builtin_profile_path`` accepts both ``glm-5.1`` and the
        full filename stem ``65-glm-5.1`` — both should hydrate."""
        a = _load_bundled_profile_base("glm-5.1")
        b = _load_bundled_profile_base("65-glm-5.1")
        assert a is not None and b is not None
        # Same bundled file → same flag values.
        assert a.malformed_tool_retry == b.malformed_tool_retry
        assert a.empty_compile_retry == b.empty_compile_retry


# ── End-to-end loader inheritance ────────────────────────────────────


class TestStaleLocalProfileInheritsFromBundled:
    """The whole point of v2.12.1: a user with a v2.9-era local copy
    of ``glm-5.1.toml`` automatically picks up all fields added in
    v2.10+ from the wheel-bundled built-in."""

    def test_stale_glm_inherits_v212_malformed_tool_retry(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            user_dir = Path(td)
            (user_dir / "glm-5.1.toml").write_text(_stale_glm_toml_text())
            registry = ProfileRegistry(user_profile_dir=user_dir)
            profile = registry._profiles["glm-5.1"]
            # The user's TOML overrides take effect…
            assert profile.name == "GLM-5.1 (stale v2.9-era copy)"
            # …but every field the stale copy doesn't mention inherits
            # from the bundled v2.12.0 built-in.
            assert profile.malformed_tool_retry is True, (
                "stale user profile must inherit malformed_tool_retry "
                "from bundled built-in (v2.12.0)"
            )
            assert profile.empty_compile_retry is True, (
                "stale user profile must inherit empty_compile_retry "
                "from bundled built-in (v2.11.0)"
            )
            assert profile.compress_old_tool_results is True, (
                "stale user profile must inherit compress_old_tool_results "
                "from bundled built-in (v2.9.0)"
            )
            assert profile.enable_parallel_tools is True
            assert profile.compile_after_tool_calls == 3

    def test_user_explicit_override_wins_over_bundled(self) -> None:
        """User's TOML still has final say — a user who explicitly
        sets ``malformed_tool_retry = false`` keeps that override."""
        with tempfile.TemporaryDirectory() as td:
            user_dir = Path(td)
            (user_dir / "glm-5.1.toml").write_text(
                _stale_glm_toml_text()
                + '\n[tool_consumption]\nmalformed_tool_retry = false\n'
            )
            registry = ProfileRegistry(user_profile_dir=user_dir)
            profile = registry._profiles["glm-5.1"]
            # Explicit user override wins.
            assert profile.malformed_tool_retry is False
            # But other v2.11/2.12 fields still inherit.
            assert profile.empty_compile_retry is True

    def test_genuine_custom_profile_falls_back_to_default(self) -> None:
        """A user with a profile name not matching any bundled built-in
        (e.g. ``my-private-model.toml``) merges over ``_DEFAULT_PROFILE``
        — pre-v2.12.1 byte-parity for the genuine custom path."""
        with tempfile.TemporaryDirectory() as td:
            user_dir = Path(td)
            (user_dir / "my-private-model.toml").write_text(
                'name = "Custom"\n'
                '[provider]\ntype = "openai-compat"\n'
            )
            registry = ProfileRegistry(user_profile_dir=user_dir)
            profile = registry._profiles["my-private-model"]
            # Custom profile gets defaults, NOT GLM's flags.
            assert profile.malformed_tool_retry == _DEFAULT_PROFILE.malformed_tool_retry
            assert profile.empty_compile_retry == _DEFAULT_PROFILE.empty_compile_retry
            assert profile.compress_old_tool_results == _DEFAULT_PROFILE.compress_old_tool_results

    def test_no_user_dir_does_not_break(self) -> None:
        """When the user has no ``~/.llmcode/model_profiles`` dir,
        the registry initialises cleanly with just hardcoded built-ins
        (no exception, no spurious bundled merges)."""
        with tempfile.TemporaryDirectory() as td:
            missing_dir = Path(td) / "does-not-exist"
            registry = ProfileRegistry(user_profile_dir=missing_dir)
            # Hardcoded built-ins still present.
            assert len(registry._profiles) > 0


# ── v2.13.3 hotfix — GLM profile carries glm_hybrid variant ─────────


class TestV2133GlmProfileVariantsList:
    """v2.13.3 — codex stop-time review (4th true positive of session)
    caught that v2.13.2's parser-registry change did NOT reach the
    GLM runtime path because the GLM profile carries an EXPLICIT
    ``[parser] variants`` list (v13 profile-driven adapter design)
    that overrides ``DEFAULT_VARIANT_ORDER`` in ``parsing._parse_xml``.
    Without ``glm_hybrid`` in the profile's list, the malformed
    parallel-emission shape stays unrecognised even after upgrade.

    These tests pin the GLM profile's variant list so a future PR
    that drops ``glm_hybrid`` (or another release adds a variant
    without updating the profile) fails CI loudly.
    """

    def test_bundled_glm_profile_includes_glm_hybrid(self) -> None:
        """Pin the actual shipped TOML — both bundled and example
        copies must carry ``glm_hybrid`` in their variants list."""
        bundled = _load_bundled_profile_base("glm-5.1")
        assert bundled is not None
        assert "glm_hybrid" in bundled.parser_variants, (
            f"GLM bundled profile missing glm_hybrid in variants — "
            f"v2.13.2 parser-registry change won't reach runtime. "
            f"Got: {bundled.parser_variants!r}"
        )

    def test_glm_hybrid_position_between_harmony_and_glm_brace(self) -> None:
        """Variant order matters — glm_hybrid must come AFTER
        harmony_kv (so proper harmony emissions extract first) and
        BEFORE glm_brace (so the hybrid shape is tried before the
        ``NAME}{JSON}`` matcher rejects it)."""
        bundled = _load_bundled_profile_base("glm-5.1")
        assert bundled is not None
        variants = list(bundled.parser_variants)
        harmony_idx = variants.index("harmony_kv")
        hybrid_idx = variants.index("glm_hybrid")
        glm_brace_idx = variants.index("glm_brace")
        assert harmony_idx < hybrid_idx < glm_brace_idx, (
            f"glm_hybrid must be between harmony_kv and glm_brace; "
            f"got order: {variants!r}"
        )

    def test_glm_profile_extracts_hybrid_shape_end_to_end(self) -> None:
        """End-to-end regression — load the GLM profile via the
        bundled-built-in path, feed the real captured malformed
        shape, assert extraction succeeds. This is the test that
        would have caught v2.13.2's profile-bypass gap before
        codex did."""
        from llm_code.tools.parsing import parse_tool_calls

        profile = _load_bundled_profile_base("glm-5.1")
        assert profile is not None

        sample = (
            '<tool_call>web_search<arg_key>args": '
            '{"query": "今日熱門新聞 2026年4月29日", "max_results": 10}}'
            '</arg_value>'
            '\u2192'
            '<tool_call>web_search<arg_key>args": '
            '{"query": "hot news today April 29 2026", "max_results": 10}}'
            '</arg_value>'
        )
        calls = parse_tool_calls(sample, None, profile=profile)
        assert len(calls) == 2, (
            f"GLM profile + glm_hybrid variant must extract both "
            f"parallel calls; got {len(calls)}: {calls!r}"
        )
        assert all(c.name == "web_search" for c in calls)
        assert calls[0].args["query"] == "今日熱門新聞 2026年4月29日"
        assert calls[1].args["query"] == "hot news today April 29 2026"

    def test_stale_local_glm_inherits_glm_hybrid(self) -> None:
        """v2.12.1 loader inheritance — stale user copy lacking the
        v2.13.3 ``glm_hybrid`` entry should pick it up from the
        bundled built-in (same self-healing path that fixed the
        v2.10–v2.12 field-delivery gap)."""
        with tempfile.TemporaryDirectory() as td:
            user_dir = Path(td)
            (user_dir / "glm-5.1.toml").write_text(_stale_glm_toml_text())
            registry = ProfileRegistry(user_profile_dir=user_dir)
            profile = registry._profiles["glm-5.1"]
            # The stale user copy doesn't even mention parser_variants;
            # inheritance pulls the bundled list (with glm_hybrid).
            assert "glm_hybrid" in profile.parser_variants, (
                f"Stale local GLM profile must inherit glm_hybrid "
                f"from bundled built-in via v2.12.1 loader. "
                f"Got: {profile.parser_variants!r}"
            )
