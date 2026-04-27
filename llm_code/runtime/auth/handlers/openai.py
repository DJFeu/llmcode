"""OpenAI API key handler (v16 M6)."""
from __future__ import annotations

from typing import ClassVar

from llm_code.runtime.auth.handlers._base import ApiKeyHandler


class OpenAIHandler(ApiKeyHandler):
    provider_name: ClassVar[str] = "openai"
    display_name: ClassVar[str] = "OpenAI"
    env_var: ClassVar[str] = "OPENAI_API_KEY"
    api_key_help_url: ClassVar[str] = "https://platform.openai.com/api-keys"
