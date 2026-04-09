"""Tests for the model profile system."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from llm_code.runtime.model_profile import (
    ModelProfile,
    ProfileRegistry,
    _DEFAULT_PROFILE,
    _profile_from_dict,
    get_profile,
)


# ---------- ModelProfile dataclass ----------


class TestModelProfile:
    def test_frozen(self) -> None:
        p = ModelProfile(name="test")
        with pytest.raises(Exception):
            p.name = "mutated"  # type: ignore[misc]

    def test_defaults(self) -> None:
        p = ModelProfile()
        assert p.name == ""
        assert p.provider_type == "openai-compat"
        assert p.native_tools is True
        assert p.supports_reasoning is False
        assert p.force_xml_tools is False
        assert p.implicit_thinking is False
        assert p.price_input == 0.0
        assert p.context_window == 128000


# ---------- Profile resolution ----------


class TestProfileRegistry:
    def test_exact_match(self) -> None:
        reg = ProfileRegistry(user_profile_dir=Path("/nonexistent"))
        p = reg.resolve("claude-sonnet-4-6")
        assert p.name == "Claude Sonnet 4.6"
        assert p.provider_type == "anthropic"
        assert p.native_tools is True

    def test_exact_match_case_insensitive(self) -> None:
        reg = ProfileRegistry(user_profile_dir=Path("/nonexistent"))
        p = reg.resolve("Claude-Sonnet-4-6")
        assert p.name == "Claude Sonnet 4.6"

    def test_prefix_match_qwen(self) -> None:
        reg = ProfileRegistry(user_profile_dir=Path("/nonexistent"))
        p = reg.resolve("Qwen3.5-122B-A10B")
        assert p.name == "Qwen3.5-122B-A10B"
        assert p.force_xml_tools is True
        assert p.implicit_thinking is True

    def test_prefix_match_qwen_small(self) -> None:
        reg = ProfileRegistry(user_profile_dir=Path("/nonexistent"))
        p = reg.resolve("qwen3:1.7b")
        assert p.name == "Qwen3"

    def test_family_default_claude(self) -> None:
        """Unknown claude model falls back to claude family default."""
        reg = ProfileRegistry(user_profile_dir=Path("/nonexistent"))
        p = reg.resolve("claude-future-model-9")
        assert p.provider_type == "anthropic"
        assert p.supports_reasoning is True

    def test_family_default_gpt(self) -> None:
        reg = ProfileRegistry(user_profile_dir=Path("/nonexistent"))
        p = reg.resolve("gpt-5-turbo")
        assert p.provider_type == "openai-compat"
        assert p.supports_images is True

    def test_family_default_qwen(self) -> None:
        reg = ProfileRegistry(user_profile_dir=Path("/nonexistent"))
        p = reg.resolve("qwen-future-99b")
        assert p.force_xml_tools is True
        assert p.implicit_thinking is True

    def test_unknown_model_returns_default(self) -> None:
        reg = ProfileRegistry(user_profile_dir=Path("/nonexistent"))
        p = reg.resolve("totally-unknown-model")
        assert p.name == "(default)"
        assert p.native_tools is True  # safe default

    def test_longest_prefix_wins(self) -> None:
        """qwen3.5-122b should match over qwen3.5 and qwen3."""
        reg = ProfileRegistry(user_profile_dir=Path("/nonexistent"))
        p = reg.resolve("qwen3.5-122b-something")
        assert p.name == "Qwen3.5-122B-A10B"  # most specific

    def test_extra_profiles(self) -> None:
        custom = {"my-model": ModelProfile(name="Custom", price_input=99.0)}
        reg = ProfileRegistry(
            user_profile_dir=Path("/nonexistent"),
            extra_profiles=custom,
        )
        p = reg.resolve("my-model")
        assert p.name == "Custom"
        assert p.price_input == 99.0

    def test_list_profiles(self) -> None:
        reg = ProfileRegistry(user_profile_dir=Path("/nonexistent"))
        profiles = reg.list_profiles()
        assert "claude-sonnet-4-6" in profiles
        assert "qwen3.5-122b" in profiles


# ---------- TOML loading ----------


class TestTomlLoading:
    def test_load_user_profile(self, tmp_path: Path) -> None:
        toml_file = tmp_path / "my-local-model.toml"
        toml_file.write_text(textwrap.dedent("""\
            name = "My Local Model"
            native_tools = false
            force_xml_tools = true
            supports_reasoning = true

            [pricing]
            price_input = 0.0
            price_output = 0.0
        """))

        reg = ProfileRegistry(user_profile_dir=tmp_path)
        p = reg.resolve("my-local-model")
        assert p.name == "My Local Model"
        assert p.native_tools is False
        assert p.force_xml_tools is True
        assert p.supports_reasoning is True

    def test_user_override_merges_with_builtin(self, tmp_path: Path) -> None:
        """User TOML overrides specific fields, keeps rest from built-in."""
        toml_file = tmp_path / "claude-sonnet-4-6.toml"
        toml_file.write_text(textwrap.dedent("""\
            default_thinking_budget = 50000
        """))

        reg = ProfileRegistry(user_profile_dir=tmp_path)
        p = reg.resolve("claude-sonnet-4-6")
        # Overridden field
        assert p.default_thinking_budget == 50000
        # Preserved from built-in
        assert p.name == "Claude Sonnet 4.6"
        assert p.provider_type == "anthropic"
        assert p.price_input == 3.00

    def test_malformed_toml_is_skipped(self, tmp_path: Path) -> None:
        toml_file = tmp_path / "bad.toml"
        toml_file.write_text("this is not valid [[[toml")

        # Should not raise
        reg = ProfileRegistry(user_profile_dir=tmp_path)
        p = reg.resolve("bad")
        # Falls through to default since the bad profile wasn't loaded
        assert p.name == "(default)"

    def test_nested_toml_sections(self, tmp_path: Path) -> None:
        toml_file = tmp_path / "custom-model.toml"
        toml_file.write_text(textwrap.dedent("""\
            name = "Custom"

            [streaming]
            implicit_thinking = true
            reasoning_field = "reasoning_content"

            [thinking]
            default_thinking_budget = 20000

            [pricing]
            price_input = 1.50
            price_output = 5.00

            [limits]
            max_output_tokens = 8192
            context_window = 65536
        """))

        reg = ProfileRegistry(user_profile_dir=tmp_path)
        p = reg.resolve("custom-model")
        assert p.name == "Custom"
        assert p.implicit_thinking is True
        assert p.reasoning_field == "reasoning_content"
        assert p.default_thinking_budget == 20000
        assert p.price_input == 1.50
        assert p.price_output == 5.00
        assert p.max_output_tokens == 8192
        assert p.context_window == 65536


# ---------- _profile_from_dict ----------


class TestProfileFromDict:
    def test_flat_dict(self) -> None:
        p = _profile_from_dict({"name": "Test", "native_tools": False})
        assert p.name == "Test"
        assert p.native_tools is False
        # Defaults preserved
        assert p.provider_type == "openai-compat"

    def test_merge_over_base(self) -> None:
        base = ModelProfile(name="Base", price_input=10.0, price_output=20.0)
        p = _profile_from_dict({"price_input": 5.0}, base=base)
        assert p.name == "Base"  # from base
        assert p.price_input == 5.0  # overridden
        assert p.price_output == 20.0  # from base

    def test_unknown_keys_ignored(self) -> None:
        p = _profile_from_dict({"name": "Test", "unknown_field": 42})
        assert p.name == "Test"


# ---------- get_profile (global) ----------


class TestGetProfile:
    def test_global_function(self) -> None:
        p = get_profile("claude-opus-4-6")
        assert p.name == "Claude Opus 4.6"
        assert p.price_input == 15.00

    def test_global_function_qwen(self) -> None:
        p = get_profile("Qwen3.5-122B-A10B")
        assert p.force_xml_tools is True
        assert p.implicit_thinking is True
        assert p.reasoning_field == "reasoning_content"


# ---------- Specific profile assertions ----------


class TestBuiltinProfiles:
    def test_qwen_122b_profile(self) -> None:
        p = get_profile("qwen3.5-122b")
        assert p.native_tools is False
        assert p.force_xml_tools is True
        assert p.implicit_thinking is True
        assert p.supports_reasoning is True
        assert p.reasoning_field == "reasoning_content"
        assert p.thinking_extra_body_format == "chat_template_kwargs"

    def test_claude_sonnet_profile(self) -> None:
        p = get_profile("claude-sonnet-4-6")
        assert p.native_tools is True
        assert p.supports_reasoning is True
        assert p.supports_images is True
        assert p.thinking_extra_body_format == "anthropic_native"
        assert p.price_input == 3.00

    def test_deepseek_r1_profile(self) -> None:
        p = get_profile("deepseek-r1")
        assert p.native_tools is False
        assert p.force_xml_tools is True
        assert p.implicit_thinking is True
        assert p.reasoning_field == "reasoning_content"

    def test_gpt4o_profile(self) -> None:
        p = get_profile("gpt-4o")
        assert p.native_tools is True
        assert p.supports_reasoning is False
        assert p.supports_images is True
        assert p.price_input == 2.50
