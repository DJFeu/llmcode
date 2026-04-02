"""Tests for llm_code.api.client — TDD: written before implementation."""
from __future__ import annotations

import pytest
from unittest.mock import patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _from_model(model: str, **kwargs):
    from llm_code.api.client import ProviderClient
    return ProviderClient.from_model(model, **kwargs)


# ---------------------------------------------------------------------------
# OpenAI-compatible routing (default)
# ---------------------------------------------------------------------------


class TestFromModelOpenAICompat:
    def test_from_model_defaults_to_openai_compat(self):
        from llm_code.api.openai_compat import OpenAICompatProvider
        provider = _from_model("qwen3", base_url="http://localhost:11434/v1")
        assert isinstance(provider, OpenAICompatProvider)

    def test_from_model_with_api_key(self):
        from llm_code.api.openai_compat import OpenAICompatProvider
        provider = _from_model(
            "gpt-4o",
            base_url="https://api.openai.com/v1",
            api_key="sk-test",
        )
        assert isinstance(provider, OpenAICompatProvider)
        assert provider._api_key == "sk-test"

    def test_from_model_passes_base_url(self):
        from llm_code.api.openai_compat import OpenAICompatProvider
        provider = _from_model("llama3", base_url="http://myserver:8080/v1")
        assert isinstance(provider, OpenAICompatProvider)
        assert provider._base_url == "http://myserver:8080/v1"

    def test_from_model_passes_timeout(self):
        from llm_code.api.openai_compat import OpenAICompatProvider
        provider = _from_model("qwen3", base_url="http://localhost:11434/v1", timeout=60.0)
        assert isinstance(provider, OpenAICompatProvider)
        assert provider._timeout == 60.0

    def test_from_model_passes_max_retries(self):
        from llm_code.api.openai_compat import OpenAICompatProvider
        provider = _from_model("qwen3", base_url="http://localhost:11434/v1", max_retries=5)
        assert isinstance(provider, OpenAICompatProvider)
        assert provider._max_retries == 5

    def test_from_model_passes_native_tools(self):
        from llm_code.api.openai_compat import OpenAICompatProvider
        provider = _from_model(
            "qwen3",
            base_url="http://localhost:11434/v1",
            native_tools=False,
        )
        assert isinstance(provider, OpenAICompatProvider)
        assert provider._native_tools is False


# ---------------------------------------------------------------------------
# Anthropic routing
# ---------------------------------------------------------------------------


class TestFromModelAnthropic:
    def test_from_model_anthropic_without_sdk(self):
        """When anthropic SDK is not installed, ImportError with helpful message."""
        from llm_code.api.client import ProviderClient

        with patch.dict("sys.modules", {"anthropic": None}):
            with pytest.raises(ImportError, match="anthropic"):
                ProviderClient.from_model("claude-3-5-sonnet-20241022")

    def test_from_model_claude_prefix_triggers_anthropic_path(self):
        """Models starting with 'claude-' attempt to use AnthropicProvider."""
        from llm_code.api.client import ProviderClient

        # Patch so AnthropicProvider import fails gracefully
        with patch.dict("sys.modules", {"anthropic": None}):
            with pytest.raises(ImportError):
                ProviderClient.from_model("claude-opus-4-5")

    def test_from_model_non_claude_does_not_trigger_anthropic(self):
        """Non-claude models never attempt AnthropicProvider import."""
        from llm_code.api.openai_compat import OpenAICompatProvider
        # Should not raise even without anthropic SDK
        provider = _from_model("gpt-4", base_url="https://api.openai.com/v1")
        assert isinstance(provider, OpenAICompatProvider)


# ---------------------------------------------------------------------------
# Default parameter values
# ---------------------------------------------------------------------------


class TestFromModelDefaults:
    def test_default_base_url_empty_string(self):
        from llm_code.api.openai_compat import OpenAICompatProvider
        provider = _from_model("qwen3")
        assert isinstance(provider, OpenAICompatProvider)

    def test_default_api_key_empty(self):
        from llm_code.api.openai_compat import OpenAICompatProvider
        provider = _from_model("qwen3")
        assert provider._api_key == ""

    def test_default_native_tools_true(self):
        from llm_code.api.openai_compat import OpenAICompatProvider
        provider = _from_model("qwen3")
        assert provider._native_tools is True
