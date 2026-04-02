"""Tests for model alias resolution."""
from __future__ import annotations

import pytest

from llm_code.runtime.model_aliases import resolve_model, BUILTIN_ALIASES


class TestBuiltinAliases:
    def test_short_gpt4o_alias(self) -> None:
        assert resolve_model("4o") == "gpt-4o"

    def test_gpt4_alias(self) -> None:
        assert resolve_model("gpt4") == "gpt-4o"

    def test_gpt4o_alias(self) -> None:
        assert resolve_model("gpt4o") == "gpt-4o"

    def test_gpt_mini_alias(self) -> None:
        assert resolve_model("gpt-mini") == "gpt-4o-mini"

    def test_4o_mini_alias(self) -> None:
        assert resolve_model("4o-mini") == "gpt-4o-mini"

    def test_opus_alias(self) -> None:
        assert resolve_model("opus") == "claude-opus-4-6"

    def test_sonnet_alias(self) -> None:
        assert resolve_model("sonnet") == "claude-sonnet-4-6"

    def test_haiku_alias(self) -> None:
        assert resolve_model("haiku") == "claude-haiku-4-5"

    def test_claude_alias(self) -> None:
        assert resolve_model("claude") == "claude-sonnet-4-6"

    def test_qwen_alias(self) -> None:
        assert resolve_model("qwen") == "qwen3.5"

    def test_qwen_large_alias(self) -> None:
        assert resolve_model("qwen-large") == "Qwen3.5-122B-A10B"

    def test_o3_alias_passthrough(self) -> None:
        # o3 is mapped to itself
        assert resolve_model("o3") == "o3"

    def test_o4_mini_alias(self) -> None:
        assert resolve_model("o4-mini") == "o4-mini"


class TestCustomAliases:
    def test_custom_alias_overrides_builtin(self) -> None:
        custom = {"sonnet": "claude-sonnet-special"}
        assert resolve_model("sonnet", custom_aliases=custom) == "claude-sonnet-special"

    def test_custom_alias_new_key(self) -> None:
        custom = {"my-model": "some-provider/my-model-v2"}
        assert resolve_model("my-model", custom_aliases=custom) == "some-provider/my-model-v2"

    def test_custom_alias_none_falls_back_to_builtin(self) -> None:
        assert resolve_model("opus", custom_aliases=None) == "claude-opus-4-6"

    def test_custom_alias_empty_dict_falls_back_to_builtin(self) -> None:
        assert resolve_model("haiku", custom_aliases={}) == "claude-haiku-4-5"


class TestUnknownModel:
    def test_unknown_model_returns_as_is(self) -> None:
        assert resolve_model("my-custom-llm") == "my-custom-llm"

    def test_full_model_name_unchanged(self) -> None:
        assert resolve_model("gpt-4o") == "gpt-4o"

    def test_full_claude_name_unchanged(self) -> None:
        assert resolve_model("claude-sonnet-4-6") == "claude-sonnet-4-6"

    def test_empty_string_returns_empty(self) -> None:
        assert resolve_model("") == ""
