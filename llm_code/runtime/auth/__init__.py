"""Provider credential storage + auth handler registry (v16 M6).

llmcode supports many providers; before v16 each one read its API key
from a hard-coded env var (``OPENAI_API_KEY``, ``ANTHROPIC_API_KEY``,
etc.). M6 adds a unified ``AuthHandler`` Protocol + on-disk credential
store (``~/.llmcode/auth/<provider>.json``, mode 0600) so the
``/auth`` slash command can manage logins consistently.

Design:

* **Storage** — flat JSON files, one per provider, mode 0600 enforced
  on write and re-checked on read. Wider permissions trigger a warning
  + skip (the handler treats the file as absent).
* **Registry** — :func:`get_handler` returns a :class:`AuthHandler`
  instance for a known provider name; unknown providers raise
  :class:`UnknownProviderError`.
* **Env var override** — provider HTTP clients always check env vars
  first (``OPENAI_API_KEY`` etc.). The auth handler is the fallback.
  This preserves the v2.5.x power-user pattern where a one-shot env
  var trumps the persisted login.
* **No credential leaks in logs** — :func:`redact` masks secrets to
  the last 4 characters (``****abcd``) so log scrapers + verbose
  mode never see a full key.

The Protocol is deliberately stdio-only: handlers print prompts via
``input()`` and return :class:`AuthResult`. Tests inject mocked handler
instances directly into the registry for deterministic flows.
"""
from __future__ import annotations

import json
import logging
import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Iterable, Mapping, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


class AuthError(Exception):
    """Base class for auth-package errors.

    Subclasses cover unknown providers, malformed credential files,
    and OAuth flow failures so callers can ``except`` on the most
    specific class they care about.
    """


class UnknownProviderError(AuthError):
    """Raised when a caller asks for a provider with no registered handler."""


class CredentialFileError(AuthError):
    """Raised when a credential file is unreadable, world-readable, or malformed."""


@dataclass(frozen=True)
class AuthResult:
    """Outcome of a successful login attempt.

    ``credentials`` is opaque-shaped (each handler stores what it needs:
    API key, OAuth tokens, expiry, refresh token, etc.). The auth
    storage layer doesn't introspect the dict — handlers own the
    schema.
    """

    method: str  # "api_key" | "oauth" | "device_code" | "free_tier"
    credentials: Mapping[str, str] = field(default_factory=dict)
    note: str = ""


@dataclass(frozen=True)
class AuthStatus:
    """Snapshot of a provider's stored credentials.

    ``logged_in`` is ``True`` when the storage file exists, is readable
    with the right mode, and has parseable JSON. ``redacted_token`` is
    a non-secret summary suitable for human display.
    """

    provider: str
    logged_in: bool
    method: str = ""
    redacted_token: str = ""
    note: str = ""


@runtime_checkable
class AuthHandler(Protocol):
    """Per-provider login handler.

    Implementations live under :mod:`llm_code.runtime.auth.handlers` —
    each subclass declares ``provider_name`` and the four interactive
    methods. ``credentials_for_request`` returns the headers a
    provider HTTP client should add to outbound requests.
    """

    provider_name: ClassVar[str]
    display_name: ClassVar[str]
    env_var: ClassVar[str]  # primary env var override (e.g. OPENAI_API_KEY)

    def login(self) -> AuthResult: ...

    def logout(self) -> None: ...

    def status(self) -> AuthStatus: ...

    def credentials_for_request(self) -> dict[str, str]: ...


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


def _auth_dir() -> Path:
    """Return the credential storage directory, creating it on demand.

    Mode 0700 on the directory + mode 0600 on every file inside it.
    Override via ``LLMCODE_AUTH_DIR`` for tests.
    """
    override = os.environ.get("LLMCODE_AUTH_DIR")
    if override:
        path = Path(override).expanduser()
    else:
        path = Path.home() / ".llmcode" / "auth"
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        # Some filesystems (Windows, some FUSE mounts) reject chmod;
        # the file-mode check below still gates per-file permissions.
        pass
    return path


def _credential_file(provider: str) -> Path:
    return _auth_dir() / f"{provider}.json"


def _is_world_readable(path: Path) -> bool:
    """Return True if the credential file is readable beyond the owner."""
    try:
        mode = path.stat().st_mode
    except OSError:
        return False
    return bool(mode & (stat.S_IRGRP | stat.S_IROTH | stat.S_IWGRP | stat.S_IWOTH))


def save_credentials(provider: str, payload: Mapping[str, Any]) -> Path:
    """Write ``payload`` to ``~/.llmcode/auth/<provider>.json`` with mode 0600.

    Handlers call this from inside ``login()``. The dict is written
    via ``json.dumps`` (sorted keys for stable diffs); the file's mode
    is forced to 0600 even if umask would have produced something
    looser.
    """
    path = _credential_file(provider)
    text = json.dumps(dict(payload), indent=2, sort_keys=True)
    # Write atomically: temp file → chmod → rename. Avoids exposing
    # the secret on disk with a wider permission window.
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(text, encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        # Best-effort on filesystems without POSIX modes; the file
        # still moves atomically into place.
        pass
    tmp.replace(path)
    logger.info("login provider=%s method=%s", provider, payload.get("method", "api_key"))
    return path


def load_credentials(provider: str) -> dict[str, Any] | None:
    """Read stored credentials for ``provider`` or return None.

    Returns ``None`` when:

    * The file does not exist.
    * The file is more permissive than 0600 — emits a warning and
      treats the credentials as absent so a wide-open file never
      leaks into runtime traffic.
    * The file is unreadable or malformed JSON — emits a warning.
    """
    path = _credential_file(provider)
    if not path.exists():
        return None
    if _is_world_readable(path):
        logger.warning(
            "credential file %s has wider mode than 0600; "
            "treating credentials as absent. Run: chmod 600 %s",
            path, path,
        )
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("credential file %s unreadable: %s", path, exc)
        return None


def clear_credentials(provider: str) -> bool:
    """Delete the stored credential file. Returns True if a file was removed."""
    path = _credential_file(provider)
    if not path.exists():
        return False
    # Zero the file before unlinking so a recovered inode doesn't
    # reveal the secret.
    try:
        size = path.stat().st_size
        with path.open("r+b") as f:
            f.seek(0)
            f.write(b"\x00" * size)
            f.flush()
            os.fsync(f.fileno())
    except OSError:
        pass
    try:
        path.unlink()
    except OSError:
        return False
    logger.info("logout provider=%s", provider)
    return True


# ---------------------------------------------------------------------------
# Redaction (cross-cutting helper used by every handler)
# ---------------------------------------------------------------------------


_API_KEY_PATTERN = re.compile(r"\b(?:sk|tok|key)[-_][A-Za-z0-9_-]{8,}\b")


def redact(secret: str) -> str:
    """Return a display-safe summary of a credential.

    Shows only the last 4 characters (``****abcd``). Used by
    :class:`AuthStatus` and any user-facing log line.
    """
    if not secret:
        return ""
    if len(secret) <= 4:
        return "*" * len(secret)
    return "*" * (len(secret) - 4) + secret[-4:]


def assert_no_credential_leak(text: str, *, secrets: Iterable[str] = ()) -> None:
    """Assertion helper used by tests.

    Raises :class:`AssertionError` when ``text`` contains either:

    * Any secret in the explicit list (full match).
    * An sk-/tok-/key- shaped substring of length >= 12 (catch-all).
    """
    for secret in secrets:
        if secret and secret in text:
            raise AssertionError(f"credential leaked into log/output: {redact(secret)}")
    match = _API_KEY_PATTERN.search(text)
    if match:
        raise AssertionError(
            f"credential-shaped string leaked into log/output: "
            f"{match.group(0)[:6]}…"
        )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


_REGISTRY: dict[str, AuthHandler] = {}


def register_handler(handler: AuthHandler) -> None:
    """Register a handler class (or instance) under its ``provider_name``.

    Idempotent — re-registering with the same name overwrites. The
    handlers package's ``register_builtins()`` calls this for each of
    the six built-in providers.
    """
    name = handler.provider_name
    _REGISTRY[name] = handler


def get_handler(provider: str) -> AuthHandler:
    """Return the registered handler for ``provider`` or raise.

    The handlers module is imported lazily on first lookup so the
    auth package stays cheap to import in test-only contexts that
    don't need any live handlers.
    """
    if not _REGISTRY:
        from llm_code.runtime.auth.handlers import register_builtins

        register_builtins()
    handler = _REGISTRY.get(provider)
    if handler is None:
        known = ", ".join(sorted(_REGISTRY.keys()))
        raise UnknownProviderError(
            f"unknown provider {provider!r}. Known: {known}"
        )
    return handler


def list_providers() -> list[str]:
    """Return all registered provider names in alphabetical order."""
    if not _REGISTRY:
        from llm_code.runtime.auth.handlers import register_builtins

        register_builtins()
    return sorted(_REGISTRY.keys())


def reset_registry_for_tests() -> None:
    """Test-only helper: drop all registered handlers."""
    _REGISTRY.clear()


# ---------------------------------------------------------------------------
# Cross-cutting integration helpers used by app_state + oneshot
# ---------------------------------------------------------------------------


# Map env-var names to the provider whose stored credentials are the
# fallback. Mirrors the ``env_var`` field on every built-in handler so
# the lookup table stays a single source of truth.
_ENV_VAR_TO_PROVIDER: dict[str, str] = {
    "ANTHROPIC_API_KEY": "anthropic",
    "OPENAI_API_KEY": "openai",
    "ZHIPU_API_KEY": "zhipu",
    "NVIDIA_API_KEY": "nvidia_nim",
    "OPENROUTER_API_KEY": "openrouter",
    "DEEPSEEK_API_KEY": "deepseek",
}


def resolve_api_key(env_var: str) -> str:
    """Return the live API key for an env-var name.

    Order: explicit env var → registered handler's stored creds. Empty
    string when neither is set. The runtime's config knows only the
    ``api_key_env`` name, so this helper is the bridge that makes
    ``/auth login openai`` actually drive HTTP traffic.
    """
    direct = os.environ.get(env_var, "")
    if direct:
        return direct
    provider = _ENV_VAR_TO_PROVIDER.get(env_var)
    if not provider:
        return ""
    try:
        handler = get_handler(provider)
    except UnknownProviderError:
        return ""
    return handler.credentials_for_request().get(
        "Authorization", "",
    ).removeprefix("Bearer ") or handler.credentials_for_request().get(
        "x-api-key", "",
    )


__all__ = [
    "AuthError",
    "AuthHandler",
    "AuthResult",
    "AuthStatus",
    "CredentialFileError",
    "UnknownProviderError",
    "assert_no_credential_leak",
    "clear_credentials",
    "get_handler",
    "list_providers",
    "load_credentials",
    "redact",
    "register_handler",
    "reset_registry_for_tests",
    "resolve_api_key",
    "save_credentials",
]
