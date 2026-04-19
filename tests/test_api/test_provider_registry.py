"""Tests for the provider registry (H4).

The registry is a thin capability manifest layer on top of the existing
``ProviderClient`` factory. It lets future providers (Bedrock, Google,
Mistral, ...) be registered without touching ``client.from_model``.
"""
from __future__ import annotations

import pytest

from llm_code.api.provider_registry import (
    ProviderCapabilities,
    ProviderDescriptor,
    ProviderRegistry,
    get_registry,
    resolve_descriptor_for_model,
)


# ---------- ProviderCapabilities dataclass ----------


class TestProviderCapabilities:
    def test_frozen(self) -> None:
        c = ProviderCapabilities()
        with pytest.raises(Exception):
            c.max_context = 999  # type: ignore[misc]

    def test_safe_defaults(self) -> None:
        c = ProviderCapabilities()
        assert c.max_context == 128_000
        assert c.supports_native_tools is True
        assert c.supports_images is False
        assert c.supports_reasoning is False
        assert c.supports_prompt_cache is False
        assert c.tools_format == "openai"

    def test_custom_values(self) -> None:
        c = ProviderCapabilities(
            max_context=200_000,
            supports_native_tools=True,
            supports_images=True,
            supports_reasoning=True,
            supports_prompt_cache=True,
            tools_format="anthropic",
        )
        assert c.tools_format == "anthropic"
        assert c.max_context == 200_000


# ---------- Registry basics ----------


class TestProviderRegistry:
    def test_register_and_get(self) -> None:
        reg = ProviderRegistry()
        desc = ProviderDescriptor(
            provider_type="custom-fake",
            display_name="Fake",
            capabilities=ProviderCapabilities(max_context=42),
        )
        reg.register(desc)
        out = reg.get("custom-fake")
        assert out is desc
        assert out.capabilities.max_context == 42

    def test_get_missing_returns_none(self) -> None:
        reg = ProviderRegistry()
        assert reg.get("no-such-provider") is None

    def test_register_overwrites_existing(self) -> None:
        reg = ProviderRegistry()
        reg.register(ProviderDescriptor(
            provider_type="x",
            display_name="old",
            capabilities=ProviderCapabilities(),
        ))
        reg.register(ProviderDescriptor(
            provider_type="x",
            display_name="new",
            capabilities=ProviderCapabilities(),
        ))
        assert reg.get("x").display_name == "new"

    def test_list_providers_contains_builtins(self) -> None:
        """The global registry must ship with anthropic + openai-compat."""
        reg = get_registry()
        types = {d.provider_type for d in reg.list_descriptors()}
        assert "anthropic" in types
        assert "openai-compat" in types


# ---------- Built-in descriptors ----------


class TestBuiltinDescriptors:
    def test_anthropic_descriptor(self) -> None:
        desc = get_registry().get("anthropic")
        assert desc is not None
        assert desc.capabilities.supports_native_tools is True
        assert desc.capabilities.supports_images is True
        assert desc.capabilities.supports_reasoning is True
        assert desc.capabilities.supports_prompt_cache is True
        assert desc.capabilities.tools_format == "anthropic"

    def test_openai_compat_descriptor(self) -> None:
        desc = get_registry().get("openai-compat")
        assert desc is not None
        assert desc.capabilities.supports_native_tools is True
        assert desc.capabilities.tools_format == "openai"


# ---------- Resolution from model name ----------


class TestResolveFromModel:
    def test_resolve_anthropic_model(self) -> None:
        desc = resolve_descriptor_for_model("claude-sonnet-4-6")
        assert desc is not None
        assert desc.provider_type == "anthropic"

    def test_resolve_qwen_local_model(self) -> None:
        desc = resolve_descriptor_for_model("qwen3-coder-7b")
        assert desc is not None
        assert desc.provider_type == "openai-compat"

    def test_resolve_unknown_falls_back_to_openai_compat(self) -> None:
        """Unknown models fall through the default profile, which is
        provider_type=openai-compat."""
        desc = resolve_descriptor_for_model("totally-unknown-model-xyz")
        assert desc is not None
        assert desc.provider_type == "openai-compat"
