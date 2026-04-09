"""Provider client factory — routes model names to the correct provider."""
from __future__ import annotations

from llm_code.api.provider import LLMProvider
from llm_code.runtime.model_aliases import resolve_model


class ProviderClient:
    """Factory for creating LLMProvider instances based on model name."""

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
        model = resolve_model(model, custom_aliases)

        from llm_code.runtime.model_profile import get_profile
        profile = get_profile(model)

        if profile.provider_type == "anthropic":
            return ProviderClient._make_anthropic(
                model=model,
                api_key=api_key,
                timeout=timeout,
                max_retries=max_retries,
            )

        # Profile overrides native_tools when explicitly declared.
        # Built-in Qwen profiles set native_tools=False so the
        # caller's default (True) doesn't override the profile.
        effective_native_tools = profile.native_tools if profile.name else native_tools

        return ProviderClient._make_openai_compat(
            model=model,
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
