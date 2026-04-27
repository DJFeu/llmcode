"""NVIDIA NIM auth handler with free-tier rate detection (v16 M6).

NIM offers a free tier capped at 40 requests/min for unauthenticated /
free-tier accounts. The handler stores the API key like any other
provider, but its ``status()`` surfaces "free tier active" so users
can see the rate cap inline.
"""
from __future__ import annotations

from typing import ClassVar

from llm_code.runtime.auth import AuthStatus, load_credentials
from llm_code.runtime.auth.handlers._base import ApiKeyHandler


# Heuristic: NIM personal/free-tier keys start with ``nvapi-`` followed
# by 64+ chars. Paid-tier (org) keys also use the same prefix; the
# notice below is informational, not a gate.
_FREE_TIER_PREFIXES = ("nvapi-",)


class NvidiaNimHandler(ApiKeyHandler):
    provider_name: ClassVar[str] = "nvidia_nim"
    display_name: ClassVar[str] = "NVIDIA NIM"
    env_var: ClassVar[str] = "NVIDIA_API_KEY"
    api_key_help_url: ClassVar[str] = "https://build.nvidia.com/explore/discover"

    def status(self) -> AuthStatus:
        base = super().status()
        if not base.logged_in:
            return base
        # Append a free-tier note when the key shape matches.
        creds = load_credentials(self.provider_name)
        api_key = (creds or {}).get("api_key", "")
        if any(api_key.startswith(p) for p in _FREE_TIER_PREFIXES):
            note = (
                base.note
                + (" | " if base.note else "")
                + "free-tier rate cap: 40 req/min"
            )
            return AuthStatus(
                provider=base.provider,
                logged_in=base.logged_in,
                method=base.method,
                redacted_token=base.redacted_token,
                note=note.strip(),
            )
        return base
