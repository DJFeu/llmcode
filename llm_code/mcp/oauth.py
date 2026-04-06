"""OAuth 2.0 Authorization Code + PKCE flow for MCP server authentication."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import secrets
import threading
import time
import urllib.parse
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import httpx


@dataclass
class OAuthToken:
    access_token: str
    token_type: str = "Bearer"
    expires_at: float = 0.0
    refresh_token: str = ""
    scope: str = ""

    @property
    def is_expired(self) -> bool:
        return time.time() >= self.expires_at - 60  # 60s buffer

    def to_dict(self) -> dict:
        return {
            "access_token": self.access_token,
            "token_type": self.token_type,
            "expires_at": self.expires_at,
            "refresh_token": self.refresh_token,
            "scope": self.scope,
        }

    @classmethod
    def from_dict(cls, data: dict) -> OAuthToken:
        return cls(**{k: data[k] for k in cls.__dataclass_fields__ if k in data})


class OAuthClient:
    """OAuth 2.0 with PKCE for MCP server authentication."""

    def __init__(
        self,
        client_id: str,
        authorization_url: str,
        token_url: str,
        redirect_uri: str = "http://localhost:9876/callback",
        scope: str = "",
    ) -> None:
        self._client_id = client_id
        self._auth_url = authorization_url
        self._token_url = token_url
        self._redirect_uri = redirect_uri
        self._scope = scope
        self._token_dir = Path.home() / ".llmcode" / "tokens"
        self._token_dir.mkdir(parents=True, exist_ok=True)

    def get_token(self, server_name: str) -> OAuthToken | None:
        """Get a valid token, refreshing if needed."""
        token = self._load_token(server_name)
        if token and not token.is_expired:
            return token
        if token and token.refresh_token:
            refreshed = self._refresh_token(token)
            if refreshed:
                self._save_token(server_name, refreshed)
                return refreshed
        return None

    async def authorize(self, server_name: str) -> OAuthToken:
        """Run the full OAuth PKCE flow."""
        # Generate PKCE challenge
        verifier = secrets.token_urlsafe(64)
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        state = secrets.token_urlsafe(32)

        # Build authorization URL
        params: dict[str, str] = {
            "client_id": self._client_id,
            "response_type": "code",
            "redirect_uri": self._redirect_uri,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        if self._scope:
            params["scope"] = self._scope

        auth_url = f"{self._auth_url}?{urllib.parse.urlencode(params)}"

        # Open browser
        print("Opening browser for authorization...")
        print(f"If it doesn't open, visit: {auth_url}")
        webbrowser.open(auth_url)

        # Start local server to receive callback
        code = await self._wait_for_callback(state)

        # Exchange code for token
        token = await self._exchange_code(code, verifier)
        self._save_token(server_name, token)
        return token

    async def _exchange_code(self, code: str, verifier: str) -> OAuthToken:
        """Exchange authorization code for tokens."""
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                self._token_url,
                data={
                    "grant_type": "authorization_code",
                    "client_id": self._client_id,
                    "code": code,
                    "redirect_uri": self._redirect_uri,
                    "code_verifier": verifier,
                },
            )
            resp.raise_for_status()
            data = resp.json()

        return OAuthToken(
            access_token=data["access_token"],
            token_type=data.get("token_type", "Bearer"),
            expires_at=time.time() + data.get("expires_in", 3600),
            refresh_token=data.get("refresh_token", ""),
            scope=data.get("scope", ""),
        )

    def _refresh_token(self, token: OAuthToken) -> OAuthToken | None:
        """Refresh an expired token synchronously."""
        try:
            with httpx.Client() as client:
                resp = client.post(
                    self._token_url,
                    data={
                        "grant_type": "refresh_token",
                        "client_id": self._client_id,
                        "refresh_token": token.refresh_token,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
            return OAuthToken(
                access_token=data["access_token"],
                token_type=data.get("token_type", "Bearer"),
                expires_at=time.time() + data.get("expires_in", 3600),
                refresh_token=data.get("refresh_token", token.refresh_token),
                scope=data.get("scope", token.scope),
            )
        except Exception:  # noqa: BLE001
            return None

    async def _wait_for_callback(self, expected_state: str) -> str:
        """Start a local HTTP server and wait for the OAuth callback."""
        code_holder: list[str] = []

        class CallbackHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                params = urllib.parse.parse_qs(
                    urllib.parse.urlparse(self.path).query
                )
                if (
                    params.get("state", [""])[0] == expected_state
                    and "code" in params
                ):
                    code_holder.append(params["code"][0])
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(
                        b"Authorization successful! You can close this tab."
                    )
                else:
                    self.send_response(400)
                    self.end_headers()
                    self.wfile.write(b"Invalid callback.")

            def log_message(self, *args: object) -> None:  # noqa: ANN002
                pass  # Suppress log output

        port = int(urllib.parse.urlparse(self._redirect_uri).port or 9876)
        server = HTTPServer(("localhost", port), CallbackHandler)

        thread = threading.Thread(target=server.handle_request, daemon=True)
        thread.start()

        # Wait for callback (max 120 seconds)
        for _ in range(1200):
            if code_holder:
                break
            await asyncio.sleep(0.1)

        server.server_close()

        if not code_holder:
            raise TimeoutError("OAuth callback timed out")
        return code_holder[0]

    def _save_token(self, server_name: str, token: OAuthToken) -> None:
        path = self._token_dir / f"{server_name}.json"
        path.write_text(json.dumps(token.to_dict()))

    def _load_token(self, server_name: str) -> OAuthToken | None:
        path = self._token_dir / f"{server_name}.json"
        if path.exists():
            try:
                return OAuthToken.from_dict(json.loads(path.read_text()))
            except Exception:  # noqa: BLE001
                return None
        return None
