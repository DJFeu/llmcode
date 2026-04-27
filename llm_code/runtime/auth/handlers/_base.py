"""Shared scaffolding for built-in auth handlers (v16 M6).

The default :class:`ApiKeyHandler` covers the common case: provider
takes a single bearer-style API key, stored at
``~/.llmcode/auth/<provider>.json`` and loaded by every request.
Providers with OAuth or free-tier flows subclass and override the
methods that differ.
"""
from __future__ import annotations

import getpass
import logging
import os
import sys
from typing import ClassVar, Protocol

from llm_code.runtime.auth import (
    AuthError,
    AuthResult,
    AuthStatus,
    clear_credentials,
    load_credentials,
    redact,
    save_credentials,
)

logger = logging.getLogger(__name__)


class _Prompt(Protocol):
    """Stdin-prompting strategy injected so tests can drive logins."""

    def __call__(self, message: str, *, secret: bool = False) -> str: ...


def _default_prompt(message: str, *, secret: bool = False) -> str:
    """Default prompt — uses ``getpass`` for secrets, ``input`` otherwise.

    Splitting this out lets tests inject deterministic responses
    without monkeypatching the stdlib globally.
    """
    if secret:
        return getpass.getpass(message).strip()
    sys.stdout.write(message)
    sys.stdout.flush()
    return sys.stdin.readline().strip()


class ApiKeyHandler:
    """Base class for handlers whose only credential is a bearer API key.

    Subclasses set ``provider_name`` / ``display_name`` / ``env_var``
    + override :meth:`credentials_for_request` if the provider needs a
    non-Bearer header shape. Anthropic, for instance, sends
    ``x-api-key`` — see :class:`AnthropicHandler`.
    """

    provider_name: ClassVar[str] = ""
    display_name: ClassVar[str] = ""
    env_var: ClassVar[str] = ""
    api_key_help_url: ClassVar[str] = ""

    def __init__(self, prompt: _Prompt | None = None) -> None:
        self._prompt = prompt or _default_prompt

    # ------------------------------------------------------------------
    # Login / logout / status
    # ------------------------------------------------------------------

    def login(self) -> AuthResult:
        """Prompt the user for an API key and persist it.

        Subclasses with OAuth flows override this — see
        :class:`ZhipuHandler`. The default flow shows the help URL
        (when present) so users land on the right credential page.
        """
        if self.api_key_help_url:
            sys.stdout.write(
                f"Get an API key from: {self.api_key_help_url}\n"
            )
        api_key = self._prompt(
            f"Paste your {self.display_name} API key: ", secret=True,
        )
        if not api_key:
            raise AuthError(f"{self.display_name}: empty API key")
        payload = {"method": "api_key", "api_key": api_key}
        save_credentials(self.provider_name, payload)
        return AuthResult(method="api_key", credentials={"api_key": api_key})

    def logout(self) -> None:
        clear_credentials(self.provider_name)

    def status(self) -> AuthStatus:
        # Env var is the override path; surface it as a logged-in
        # status so users see why their HTTP requests use a different
        # key than ``/auth list`` shows.
        env_value = os.environ.get(self.env_var, "")
        if env_value:
            return AuthStatus(
                provider=self.provider_name,
                logged_in=True,
                method="env_var",
                redacted_token=redact(env_value),
                note=f"env var {self.env_var} is set; overrides stored credentials",
            )
        creds = load_credentials(self.provider_name)
        if creds is None:
            return AuthStatus(provider=self.provider_name, logged_in=False)
        api_key = creds.get("api_key", "")
        return AuthStatus(
            provider=self.provider_name,
            logged_in=bool(api_key),
            method=str(creds.get("method", "api_key")),
            redacted_token=redact(api_key),
        )

    # ------------------------------------------------------------------
    # Outbound credentials
    # ------------------------------------------------------------------

    def credentials_for_request(self) -> dict[str, str]:
        """Return the HTTP header dict the provider client should add.

        Default emits ``Authorization: Bearer <key>``. Subclasses with
        non-Bearer schemes override.

        Env var override always wins over stored credentials. This is
        the cross-cutting policy from the M6 spec — power users keep
        the env-var muscle memory unchanged.
        """
        env_value = os.environ.get(self.env_var, "")
        if env_value:
            return {"Authorization": f"Bearer {env_value}"}
        creds = load_credentials(self.provider_name)
        if not creds:
            return {}
        api_key = creds.get("api_key", "")
        if not api_key:
            return {}
        return {"Authorization": f"Bearer {api_key}"}

    # ------------------------------------------------------------------
    # Hooks subclasses can override
    # ------------------------------------------------------------------

    def get_api_key(self) -> str:
        """Return the live API key string, env var first.

        Useful for callers that need the raw value (legacy code paths
        that pass the key directly to provider constructors).
        """
        env_value = os.environ.get(self.env_var, "")
        if env_value:
            return env_value
        creds = load_credentials(self.provider_name)
        if creds:
            return str(creds.get("api_key", ""))
        return ""
