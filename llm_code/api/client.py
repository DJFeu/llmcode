"""Provider client factory — routes model names to the correct provider."""
from __future__ import annotations

from llm_code.api.provider import LLMProvider


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
    ) -> LLMProvider:
        """Return the appropriate LLMProvider for the given model name.

        Routing rules:
        - Models starting with "claude-" → AnthropicProvider (requires
          the ``anthropic`` SDK to be installed).
        - Everything else → OpenAICompatProvider.
        """
        if model.startswith("claude-"):
            return ProviderClient._make_anthropic(
                model=model,
                api_key=api_key,
                timeout=timeout,
                max_retries=max_retries,
            )

        return ProviderClient._make_openai_compat(
            model=model,
            base_url=base_url,
            api_key=api_key,
            timeout=timeout,
            max_retries=max_retries,
            native_tools=native_tools,
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
        try:
            from llm_code.api.anthropic_provider import AnthropicProvider  # type: ignore[import]
        except ImportError:
            raise ImportError(
                "The 'anthropic' SDK is required to use Claude models. "
                "Install it with: pip install anthropic"
            )

        return AnthropicProvider(
            api_key=api_key,
            model_name=model,
            timeout=timeout,
            max_retries=max_retries,
        )
