"""Provider client factory — routes model names to the correct provider."""
from __future__ import annotations

from dataclasses import dataclass

from llm_code.api.provider import LLMProvider
from llm_code.api.provider_registry import ProviderDescriptor, get_registry
from llm_code.logging import get_logger
from llm_code.runtime.model_aliases import resolve_model
from llm_code.runtime.model_profile import ModelProfile, get_profile

logger = get_logger(__name__)


@dataclass(frozen=True)
class DescribeResult:
    """Everything callers need to know about a model's wiring.

    Returned by :meth:`ProviderClient.describe`. The ``descriptor`` is
    ``None`` only when the model's profile points at a ``provider_type``
    that has no registered :class:`ProviderDescriptor` — in which case
    ``describe`` also logs a warning so the gap is visible.
    """

    model: str
    profile: ModelProfile
    descriptor: ProviderDescriptor | None


class ProviderClient:
    """Factory for creating LLMProvider instances based on model name."""

    @classmethod
    def describe(
        cls,
        model: str,
        custom_aliases: dict[str, str] | None = None,
    ) -> DescribeResult:
        """Resolve ``model`` to its profile + registered descriptor.

        Useful for callers that need capability metadata (prompt cache
        support, tools_format, max context) without instantiating a
        provider. Logs a warning when the profile's ``provider_type``
        has no descriptor in the registry.
        """
        resolved = resolve_model(model, custom_aliases)
        profile = get_profile(resolved)
        descriptor: ProviderDescriptor | None = None
        if profile.provider_type:
            descriptor = get_registry().get(profile.provider_type)
            if descriptor is None:
                logger.warning(
                    "No ProviderDescriptor registered for provider_type=%r "
                    "(model=%r); add one via provider_registry.register() "
                    "so capability lookups resolve.",
                    profile.provider_type,
                    resolved,
                )
        return DescribeResult(model=resolved, profile=profile, descriptor=descriptor)

    @staticmethod
    def from_model(
        model: str,
        base_url: str = "",
        api_key: str = "",
        timeout: float = 120.0,
        max_retries: int = 2,
        native_tools: bool = True,
        custom_aliases: dict[str, str] | None = None,
    ) -> LLMProvider:
        """Return the appropriate LLMProvider for the given model name.

        Routing uses the model profile system: the profile's
        ``provider_type`` field determines which provider class to
        instantiate, and ``native_tools`` overrides the caller's
        default when the profile declares it.
        """
        # Go through describe() so the registry warning fires for
        # provider types that lack a descriptor — same code path for
        # lookup and instantiation keeps the two layers aligned.
        spec = ProviderClient.describe(model, custom_aliases=custom_aliases)
        resolved_model = spec.model
        profile = spec.profile

        if profile.provider_type == "anthropic":
            return ProviderClient._make_anthropic(
                model=resolved_model,
                api_key=api_key,
                timeout=timeout,
                max_retries=max_retries,
            )

        # Profile overrides native_tools when explicitly declared.
        # Built-in Qwen profiles set native_tools=False so the
        # caller's default (True) doesn't override the profile.
        effective_native_tools = profile.native_tools if profile.name else native_tools

        return ProviderClient._make_openai_compat(
            model=resolved_model,
            base_url=base_url,
            api_key=api_key,
            timeout=timeout,
            max_retries=max_retries,
            native_tools=effective_native_tools,
        )

    # ------------------------------------------------------------------
    # Private factory helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _make_openai_compat(
        model: str,
        base_url: str,
        api_key: str,
        timeout: float,
        max_retries: int,
        native_tools: bool,
    ) -> LLMProvider:
        from llm_code.api.openai_compat import OpenAICompatProvider

        return OpenAICompatProvider(
            base_url=base_url,
            api_key=api_key,
            model_name=model,
            timeout=timeout,
            max_retries=max_retries,
            native_tools=native_tools,
        )

    @staticmethod
    def _make_anthropic(
        model: str,
        api_key: str,
        timeout: float,
        max_retries: int,
    ) -> LLMProvider:
        from llm_code.api.anthropic_provider import AnthropicProvider

        return AnthropicProvider(
            api_key=api_key,
            model_name=model,
            timeout=timeout,
            max_retries=max_retries,
        )
