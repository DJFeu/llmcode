"""Provider capability manifest (H4 — Sprint 1).

A thin manifest layer sitting on top of the existing
``ProviderClient.from_model`` factory. The goal is to let future
providers (Bedrock, Mistral, Google Vertex, ...) declare their
capabilities in one place without touching the factory's routing code.

Non-goals for this iteration:
    * Replacing ``ProviderClient.from_model`` — the factory keeps its
      direct instantiation path for backward compatibility.
    * Carrying auth / base URL configuration — that still lives in the
      caller's config. Descriptors are about *what the provider supports*,
      not *how to talk to it*.

Usage::

    from llm_code.api.provider_registry import (
        get_registry,
        resolve_descriptor_for_model,
    )

    # Pick a descriptor for a concrete model — resolves through
    # ``ModelProfile.provider_type``.
    desc = resolve_descriptor_for_model("claude-sonnet-4-6")
    if desc.capabilities.supports_prompt_cache:
        ...

    # Register a custom provider (e.g. an in-house adapter).
    get_registry().register(
        ProviderDescriptor(
            provider_type="acme-inc",
            display_name="ACME Inc. Router",
            capabilities=ProviderCapabilities(
                max_context=262_144,
                supports_native_tools=True,
                tools_format="openai",
            ),
        )
    )
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ProviderCapabilities:
    """Declarative capabilities for a provider type.

    These are *upper bounds* of what the provider can do. Per-model
    overrides still live on ``ModelProfile`` — e.g. Haiku 4.5 inherits
    anthropic's ``supports_prompt_cache=True`` here but its own
    per-model price / context window from the profile.
    """

    # ── Context & output ─────────────────────────────────────────────
    max_context: int = 128_000
    max_output_tokens: int = 8_192

    # ── Feature flags ────────────────────────────────────────────────
    supports_native_tools: bool = True
    supports_images: bool = False
    supports_reasoning: bool = False
    supports_prompt_cache: bool = False
    supports_streaming: bool = True

    # ── Tool-call wire format ────────────────────────────────────────
    # "openai"     — OpenAI-compat function calling (most providers)
    # "anthropic"  — Anthropic-native tool_use / tool_result blocks
    # "xml"        — XML-tagged fallback (Qwen / DeepSeek OSS)
    tools_format: str = "openai"


@dataclass(frozen=True)
class ProviderDescriptor:
    """A single entry in the provider registry."""

    provider_type: str          # matches ModelProfile.provider_type
    display_name: str           # human-readable label
    capabilities: ProviderCapabilities = field(default_factory=ProviderCapabilities)


# ── Built-in descriptors ──────────────────────────────────────────────

_BUILTIN_DESCRIPTORS: tuple[ProviderDescriptor, ...] = (
    ProviderDescriptor(
        provider_type="anthropic",
        display_name="Anthropic Messages API",
        capabilities=ProviderCapabilities(
            max_context=200_000,
            max_output_tokens=16_384,
            supports_native_tools=True,
            supports_images=True,
            supports_reasoning=True,
            supports_prompt_cache=True,
            supports_streaming=True,
            tools_format="anthropic",
        ),
    ),
    ProviderDescriptor(
        provider_type="openai-compat",
        display_name="OpenAI-compatible (OpenAI, vLLM, Ollama, LM Studio, Dashscope, ...)",
        capabilities=ProviderCapabilities(
            max_context=128_000,
            max_output_tokens=8_192,
            supports_native_tools=True,
            supports_images=False,
            supports_reasoning=False,
            supports_prompt_cache=False,
            supports_streaming=True,
            tools_format="openai",
        ),
    ),
)


# ── Registry ──────────────────────────────────────────────────────────


class ProviderRegistry:
    """Mutable registry mapping ``provider_type`` → descriptor.

    Safe to construct isolated instances in tests. Prefer
    :func:`get_registry` for the process-wide singleton.
    """

    def __init__(self, include_builtins: bool = True) -> None:
        self._by_type: dict[str, ProviderDescriptor] = {}
        if include_builtins:
            for desc in _BUILTIN_DESCRIPTORS:
                self._by_type[desc.provider_type] = desc

    def register(self, descriptor: ProviderDescriptor) -> None:
        """Add or overwrite a descriptor."""
        self._by_type[descriptor.provider_type] = descriptor

    def get(self, provider_type: str) -> ProviderDescriptor | None:
        return self._by_type.get(provider_type)

    def list_descriptors(self) -> tuple[ProviderDescriptor, ...]:
        return tuple(self._by_type.values())


# ── Global singleton ──────────────────────────────────────────────────

_registry: ProviderRegistry | None = None


def get_registry() -> ProviderRegistry:
    """Return the process-wide registry, lazily initialised."""
    global _registry
    if _registry is None:
        _registry = ProviderRegistry(include_builtins=True)
    return _registry


def resolve_descriptor_for_model(model: str) -> ProviderDescriptor | None:
    """Resolve ``model`` → :class:`ProviderDescriptor` via ``ModelProfile``.

    Returns ``None`` when the model's profile points at a provider type
    that hasn't been registered. Unknown models fall through
    ``ModelProfile``'s default chain, which lands on ``"openai-compat"``.
    """
    # Local import to avoid a circular dependency at module load time.
    from llm_code.runtime.model_profile import get_profile

    profile = get_profile(model)
    if not profile.provider_type:
        return None
    return get_registry().get(profile.provider_type)
