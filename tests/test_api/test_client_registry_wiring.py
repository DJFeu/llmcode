"""Tests for ProviderClient.describe() and the registry wiring
inside ProviderClient.from_model (H4 follow-up).

``describe()`` exposes the ``(profile, descriptor)`` pair so callers
that need capability metadata (prompt cache support, tools_format,
max_context) don't have to re-resolve the profile themselves.
"""
from __future__ import annotations

import logging


from llm_code.api.client import ProviderClient


# ---------- describe() ----------


class TestDescribe:
    def test_anthropic_model(self) -> None:
        result = ProviderClient.describe("claude-sonnet-4-6")
        assert result.profile.provider_type == "anthropic"
        assert result.descriptor is not None
        assert result.descriptor.provider_type == "anthropic"
        assert result.descriptor.capabilities.supports_prompt_cache is True
        assert result.descriptor.capabilities.tools_format == "anthropic"

    def test_qwen_local_model(self) -> None:
        result = ProviderClient.describe("qwen3-coder-7b")
        assert result.profile.provider_type == "openai-compat"
        assert result.descriptor is not None
        assert result.descriptor.capabilities.tools_format == "openai"
        # The model profile is the specific Qwen-Coder-7B profile
        assert result.profile.context_window == 262144
        assert result.profile.is_local is True

    def test_unknown_model_uses_default_profile(self) -> None:
        """Unknown models fall back to the default profile whose
        provider_type is "openai-compat" — so the descriptor must
        resolve, never None."""
        result = ProviderClient.describe("zz-nonexistent-model-xyz")
        assert result.profile.provider_type == "openai-compat"
        assert result.descriptor is not None
        assert result.descriptor.provider_type == "openai-compat"


# ---------- Warning when provider_type has no descriptor ----------


class TestRegistryWarning:
    def test_missing_descriptor_warns(self, caplog, monkeypatch) -> None:
        """If a ModelProfile declares a provider_type with no registered
        descriptor, ProviderClient.describe must emit a warning so the
        gap is visible in logs."""
        from dataclasses import replace

        from llm_code.runtime import model_profile as mp

        # Build a registry that deliberately omits the "experimental"
        # provider type so the lookup returns None.
        fake_profile = replace(
            mp._DEFAULT_PROFILE,
            name="Experimental",
            provider_type="experimental-provider",
        )

        def fake_get_profile(model: str):
            return fake_profile

        monkeypatch.setattr(
            "llm_code.api.client.get_profile",
            fake_get_profile,
            raising=False,
        )
        # Also patch the import-from-within-method form used by from_model
        monkeypatch.setattr(
            "llm_code.runtime.model_profile.get_profile",
            fake_get_profile,
        )

        caplog.set_level(logging.WARNING, logger="llm_code.api.client")
        result = ProviderClient.describe("anything")
        assert result.descriptor is None
        # Warning must mention the missing provider_type
        assert any(
            "experimental-provider" in rec.getMessage()
            for rec in caplog.records
        ), f"no warning for unknown provider_type; records={caplog.records}"

    def test_registered_provider_does_not_warn(self, caplog) -> None:
        caplog.set_level(logging.WARNING, logger="llm_code.api.client")
        ProviderClient.describe("claude-sonnet-4-6")
        # Registered provider — no missing-descriptor warning
        assert not any(
            "No ProviderDescriptor" in rec.getMessage()
            for rec in caplog.records
        )


# ---------- from_model behaviour is unchanged ----------


class TestFromModelBackwardCompat:
    """describe() is new; from_model must keep returning the same
    provider class for the same inputs as before H4."""

    def test_openai_compat_still_returned_for_qwen(self) -> None:
        provider = ProviderClient.from_model(
            model="qwen3-coder-7b",
            base_url="http://localhost:11434/v1",
            api_key="sk-test",
            timeout=1.0,
            max_retries=0,
            native_tools=True,
        )
        # Type check via class name to avoid importing the class
        assert type(provider).__name__ == "OpenAICompatProvider"

    def test_anthropic_still_returned_for_claude(self) -> None:
        provider = ProviderClient.from_model(
            model="claude-sonnet-4-6",
            api_key="sk-test",
            timeout=1.0,
            max_retries=0,
        )
        assert type(provider).__name__ == "AnthropicProvider"


# ---------- describe() result type ----------


class TestDescribeResult:
    def test_result_fields_present(self) -> None:
        result = ProviderClient.describe("claude-sonnet-4-6")
        # Fields must be accessible as attributes
        assert hasattr(result, "profile")
        assert hasattr(result, "descriptor")
        assert hasattr(result, "model")
        assert result.model == "claude-sonnet-4-6"

    def test_model_resolution_applies_aliases(self) -> None:
        """describe() runs the model through resolve_model, so aliases
        declared in the user config would resolve before profile lookup.
        Without any custom aliases the input string is returned as-is."""
        result = ProviderClient.describe("claude-sonnet-4-6")
        assert result.model == "claude-sonnet-4-6"
