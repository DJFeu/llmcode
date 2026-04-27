"""OpenRouter API key handler (v16 M6)."""
from __future__ import annotations

from typing import ClassVar

from llm_code.runtime.auth.handlers._base import ApiKeyHandler


class OpenRouterHandler(ApiKeyHandler):
    provider_name: ClassVar[str] = "openrouter"
    display_name: ClassVar[str] = "OpenRouter"
    env_var: ClassVar[str] = "OPENROUTER_API_KEY"
    api_key_help_url: ClassVar[str] = "https://openrouter.ai/keys"
