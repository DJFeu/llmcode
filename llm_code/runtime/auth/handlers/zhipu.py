"""Zhipu / GLM auth handler (v16 M6).

Zhipu offers both an API key (paid plan) and an OAuth flow that lands
a token at the GLM coding plan endpoint. The handler walks the user
through one of the two; OAuth falls back to a device-code flow when
``LLMCODE_HEADLESS`` is set or no browser is reachable.

The OAuth implementation in this module is intentionally minimal: it
prints the URL + device code, polls the token endpoint, and stores
the result. We ship without an embedded HTTP server so the
``LLMCODE_HEADLESS`` path covers WSL/Docker/SSH deployments
out of the box.
"""
from __future__ import annotations

import os
import sys
from typing import ClassVar

from llm_code.runtime.auth import (
    AuthError,
    AuthResult,
    save_credentials,
)
from llm_code.runtime.auth.handlers._base import ApiKeyHandler


# Zhipu coding-plan OAuth endpoints — placeholders read from env so
# tests inject deterministic URLs and the production endpoints can be
# rotated without code edits.
_DEFAULT_DEVICE_AUTH_URL = "https://open.bigmodel.cn/oauth/device/code"
_DEFAULT_TOKEN_URL = "https://open.bigmodel.cn/oauth/token"


class ZhipuHandler(ApiKeyHandler):
    provider_name: ClassVar[str] = "zhipu"
    display_name: ClassVar[str] = "Zhipu (GLM)"
    env_var: ClassVar[str] = "ZHIPU_API_KEY"
    api_key_help_url: ClassVar[str] = "https://open.bigmodel.cn/usercenter/apikeys"

    # ------------------------------------------------------------------
    # Login flow
    # ------------------------------------------------------------------

    def login(self) -> AuthResult:
        sys.stdout.write(
            f"{self.display_name}: choose [1] API key  [2] OAuth (device code)\n"
        )
        choice = self._prompt("Selection: ").strip() or "1"
        if choice == "2":
            return self._device_code_login()
        return super().login()

    # ------------------------------------------------------------------
    # OAuth — device code flow
    # ------------------------------------------------------------------

    def _device_code_login(self) -> AuthResult:
        """Run an OAuth device-code flow and persist the resulting token.

        We deliberately use only stdlib HTTP (``urllib``) so the auth
        package stays installable without optional extras. The endpoint
        URLs are env-overridable so tests inject a local stub server.
        """
        try:
            from urllib import error as urllib_error
            from urllib import parse as urllib_parse
            from urllib import request as urllib_request
        except ImportError as exc:  # pragma: no cover - stdlib always present
            raise AuthError(f"urllib unavailable: {exc}") from exc

        device_url = os.environ.get(
            "LLMCODE_ZHIPU_DEVICE_AUTH_URL", _DEFAULT_DEVICE_AUTH_URL,
        )
        token_url = os.environ.get(
            "LLMCODE_ZHIPU_TOKEN_URL", _DEFAULT_TOKEN_URL,
        )
        client_id = os.environ.get("LLMCODE_ZHIPU_CLIENT_ID", "llmcode")

        try:
            req = urllib_request.Request(
                device_url,
                data=urllib_parse.urlencode({"client_id": client_id}).encode("utf-8"),
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            with urllib_request.urlopen(req, timeout=30) as resp:
                import json as _json
                payload = _json.loads(resp.read().decode("utf-8"))
        except (urllib_error.URLError, ValueError) as exc:
            raise AuthError(
                f"Zhipu device-code request failed: {exc}. "
                "Falling back: paste an API key via /auth login zhipu and select [1].",
            ) from exc

        device_code = str(payload.get("device_code", ""))
        user_code = str(payload.get("user_code", ""))
        verification_uri = str(payload.get("verification_uri", ""))
        if not (device_code and user_code and verification_uri):
            raise AuthError(
                f"Zhipu device-code endpoint returned incomplete response: {payload}"
            )

        sys.stdout.write(
            f"\nVisit {verification_uri} and enter code: {user_code}\n"
        )
        sys.stdout.write("Waiting for confirmation… (Ctrl-C to abort)\n")

        # Poll for the token. Real flows take 10-60 seconds; the
        # interval comes from the device endpoint or defaults to 5s.
        interval = float(payload.get("interval", 5))
        deadline = float(payload.get("expires_in", 600))

        import json as _json
        import time

        start = time.monotonic()
        while time.monotonic() - start < deadline:
            time.sleep(max(interval, 1.0))
            try:
                req = urllib_request.Request(
                    token_url,
                    data=urllib_parse.urlencode({
                        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                        "device_code": device_code,
                        "client_id": client_id,
                    }).encode("utf-8"),
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
                with urllib_request.urlopen(req, timeout=15) as resp:
                    body = _json.loads(resp.read().decode("utf-8"))
            except urllib_error.HTTPError as exc:
                # 400 ``authorization_pending`` is normal during polling.
                if exc.code != 400:
                    raise AuthError(
                        f"Zhipu token poll failed: {exc.code}"
                    ) from exc
                continue
            except urllib_error.URLError as exc:
                raise AuthError(f"Zhipu token poll failed: {exc}") from exc

            access_token = str(body.get("access_token", ""))
            if access_token:
                payload_to_save = {
                    "method": "oauth_device_code",
                    "api_key": access_token,
                    "refresh_token": str(body.get("refresh_token", "")),
                    "expires_in": body.get("expires_in", 0),
                }
                save_credentials(self.provider_name, payload_to_save)
                return AuthResult(
                    method="oauth_device_code",
                    credentials={"api_key": access_token},
                    note="Zhipu OAuth device-code flow completed",
                )

        raise AuthError("Zhipu OAuth device-code flow timed out")
