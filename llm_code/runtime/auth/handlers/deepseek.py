"""DeepSeek API key handler (v16 M6)."""
from __future__ import annotations

from typing import ClassVar

from llm_code.runtime.auth.handlers._base import ApiKeyHandler


class DeepSeekHandler(ApiKeyHandler):
    provider_name: ClassVar[str] = "deepseek"
    display_name: ClassVar[str] = "DeepSeek"
    env_var: ClassVar[str] = "DEEPSEEK_API_KEY"
    api_key_help_url: ClassVar[str] = "https://platform.deepseek.com/api_keys"
