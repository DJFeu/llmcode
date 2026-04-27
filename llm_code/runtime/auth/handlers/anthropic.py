"""Anthropic (Claude) API key handler (v16 M6).

Anthropic's HTTP API uses ``x-api-key`` instead of bearer auth so the
default :class:`ApiKeyHandler.credentials_for_request` is overridden.
"""
from __future__ import annotations

import os
from typing import ClassVar

from llm_code.runtime.auth import load_credentials
from llm_code.runtime.auth.handlers._base import ApiKeyHandler


class AnthropicHandler(ApiKeyHandler):
    provider_name: ClassVar[str] = "anthropic"
    display_name: ClassVar[str] = "Anthropic"
    env_var: ClassVar[str] = "ANTHROPIC_API_KEY"
    api_key_help_url: ClassVar[str] = "https://console.anthropic.com/settings/keys"

    def credentials_for_request(self) -> dict[str, str]:
        env_value = os.environ.get(self.env_var, "")
        if env_value:
            return {"x-api-key": env_value}
        creds = load_credentials(self.provider_name)
        if not creds:
            return {}
        api_key = str(creds.get("api_key", ""))
        if not api_key:
            return {}
        return {"x-api-key": api_key}
