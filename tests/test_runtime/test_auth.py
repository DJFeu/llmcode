"""Tests for v16 M6 auth handlers + storage + dispatcher wiring.

Covers:

* ``AuthHandler`` Protocol implementations for all six built-in
  providers (Anthropic, OpenAI, Zhipu, NVIDIA NIM, OpenRouter,
  DeepSeek).
* Storage layer: 0600 mode enforcement on write, mode-check rejection
  on read, atomic write + replace, zero-on-clear behaviour.
* Env-var override semantics.
* Credential redaction guarantees: no full key ever surfaces in
  captured logs at any verbosity.
* Dispatcher ``/auth`` command: list / status / login / logout.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import pytest

from llm_code.runtime.auth import (
    AuthError,
    AuthResult,
    UnknownProviderError,
    assert_no_credential_leak,
    clear_credentials,
    get_handler,
    list_providers,
    load_credentials,
    redact,
    reset_registry_for_tests,
    resolve_api_key,
    save_credentials,
)
from llm_code.runtime.auth.handlers import register_builtins
from llm_code.runtime.auth.handlers.anthropic import AnthropicHandler
from llm_code.runtime.auth.handlers.deepseek import DeepSeekHandler
from llm_code.runtime.auth.handlers.nvidia_nim import NvidiaNimHandler
from llm_code.runtime.auth.handlers.openai import OpenAIHandler
from llm_code.runtime.auth.handlers.openrouter import OpenRouterHandler


# ---------------------------------------------------------------------------
# Storage isolation fixture
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_auth_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("LLMCODE_AUTH_DIR", str(tmp_path / "auth"))
    # Clear every env var that the handlers consult so tests start
    # from a clean slate regardless of the developer's shell.
    for var in (
        "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "ZHIPU_API_KEY",
        "NVIDIA_API_KEY", "OPENROUTER_API_KEY", "DEEPSEEK_API_KEY",
        "LLM_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    reset_registry_for_tests()
    register_builtins()
    return tmp_path / "auth"


# ---------------------------------------------------------------------------
# Storage layer
# ---------------------------------------------------------------------------


class TestStorage:
    def test_save_creates_file_with_mode_0600(self, _isolated_auth_dir: Path) -> None:
        path = save_credentials("openai", {"method": "api_key", "api_key": "sk-test1234567890"})
        assert path.exists()
        # On POSIX, the file's permission bits are exactly 0600.
        if os.name == "posix":
            mode = path.stat().st_mode & 0o777
            assert mode == 0o600

    def test_load_returns_payload(self, _isolated_auth_dir: Path) -> None:
        save_credentials("openai", {"method": "api_key", "api_key": "sk-load"})
        creds = load_credentials("openai")
        assert creds == {"method": "api_key", "api_key": "sk-load"}

    def test_load_returns_none_when_missing(self, _isolated_auth_dir: Path) -> None:
        assert load_credentials("never-saved") is None

    def test_load_rejects_world_readable_file(
        self, _isolated_auth_dir: Path, caplog: pytest.LogCaptureFixture,
    ) -> None:
        if os.name != "posix":
            pytest.skip("permission test only meaningful on POSIX")
        save_credentials("openai", {"method": "api_key", "api_key": "sk-loose"})
        path = _isolated_auth_dir / "openai.json"
        os.chmod(path, 0o644)  # world-readable
        with caplog.at_level(logging.WARNING):
            creds = load_credentials("openai")
        assert creds is None
        assert "wider mode than 0600" in caplog.text

    def test_clear_returns_true_when_file_existed(self, _isolated_auth_dir: Path) -> None:
        save_credentials("openai", {"method": "api_key", "api_key": "sk-x"})
        assert clear_credentials("openai") is True
        assert load_credentials("openai") is None

    def test_clear_returns_false_when_no_file(self, _isolated_auth_dir: Path) -> None:
        assert clear_credentials("never-saved") is False


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------


class TestRedaction:
    def test_redact_short_string(self) -> None:
        assert redact("abc") == "***"

    def test_redact_longer_string(self) -> None:
        assert redact("sk-supersecretkey1234") == "*****************1234"

    def test_redact_empty(self) -> None:
        assert redact("") == ""

    def test_assert_no_leak_passes_for_clean_text(self) -> None:
        assert_no_credential_leak("nothing to see here")

    def test_assert_no_leak_catches_explicit_secret(self) -> None:
        with pytest.raises(AssertionError, match="leaked"):
            assert_no_credential_leak(
                "log line: hello sk-mysecret1234",
                secrets=("sk-mysecret1234",),
            )

    def test_assert_no_leak_catches_pattern_shaped(self) -> None:
        with pytest.raises(AssertionError, match="leaked"):
            assert_no_credential_leak("the key is sk-abcdefghij_xx")


# ---------------------------------------------------------------------------
# Per-handler login + status + logout
# ---------------------------------------------------------------------------


class TestPerHandlerLifecycle:
    @pytest.mark.parametrize("handler_cls", [
        OpenAIHandler,
        AnthropicHandler,
        OpenRouterHandler,
        DeepSeekHandler,
        NvidiaNimHandler,
    ])
    def test_full_login_logout_cycle(
        self, _isolated_auth_dir: Path, handler_cls: Any,
    ) -> None:
        prompts = iter(["sk-test-fixture-key"])

        def fake_prompt(message: str, *, secret: bool = False) -> str:
            return next(prompts)

        handler = handler_cls(prompt=fake_prompt)
        result = handler.login()
        assert isinstance(result, AuthResult)
        assert result.method == "api_key"

        status = handler.status()
        assert status.logged_in is True
        # Redacted token never contains the full secret.
        assert "sk-test-fixture-key" not in status.redacted_token

        handler.logout()
        post = handler.status()
        assert post.logged_in is False

    def test_credentials_for_request_uses_stored_key(
        self, _isolated_auth_dir: Path,
    ) -> None:
        prompts = iter(["sk-stored-only"])
        handler = OpenAIHandler(
            prompt=lambda msg, secret=False: next(prompts),
        )
        handler.login()
        creds = handler.credentials_for_request()
        assert creds == {"Authorization": "Bearer sk-stored-only"}

    def test_anthropic_uses_x_api_key_header(
        self, _isolated_auth_dir: Path,
    ) -> None:
        prompts = iter(["sk-ant-fixture"])
        handler = AnthropicHandler(
            prompt=lambda msg, secret=False: next(prompts),
        )
        handler.login()
        creds = handler.credentials_for_request()
        assert "Authorization" not in creds
        assert creds["x-api-key"] == "sk-ant-fixture"

    def test_login_rejects_empty_key(self, _isolated_auth_dir: Path) -> None:
        handler = OpenAIHandler(prompt=lambda msg, secret=False: "")
        with pytest.raises(AuthError, match="empty"):
            handler.login()


# ---------------------------------------------------------------------------
# Env-var override semantics
# ---------------------------------------------------------------------------


class TestEnvVarOverride:
    def test_env_var_overrides_stored_credentials(
        self, _isolated_auth_dir: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Store an API key first, then set an env var; env wins.
        prompts = iter(["sk-stored"])
        handler = OpenAIHandler(prompt=lambda msg, secret=False: next(prompts))
        handler.login()

        monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env")
        creds = handler.credentials_for_request()
        assert creds == {"Authorization": "Bearer sk-from-env"}

    def test_env_var_only_when_no_stored_key(
        self, _isolated_auth_dir: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-only-env")
        handler = OpenAIHandler()
        creds = handler.credentials_for_request()
        assert creds == {"Authorization": "Bearer sk-only-env"}

    def test_status_surfaces_env_var_override(
        self, _isolated_auth_dir: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        prompts = iter(["sk-stored"])
        OpenAIHandler(prompt=lambda msg, secret=False: next(prompts)).login()
        monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env-too")
        status = OpenAIHandler().status()
        assert status.method == "env_var"
        assert "overrides" in status.note

    def test_resolve_api_key_env_first_then_handler(
        self, _isolated_auth_dir: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Pure env var wins.
        monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
        assert resolve_api_key("OPENAI_API_KEY") == "sk-env"
        # Without env var, handler-stored key is the fallback.
        monkeypatch.delenv("OPENAI_API_KEY")
        prompts = iter(["sk-stored-fallback"])
        OpenAIHandler(prompt=lambda msg, secret=False: next(prompts)).login()
        assert resolve_api_key("OPENAI_API_KEY") == "sk-stored-fallback"

    def test_resolve_api_key_unknown_env_var(
        self, _isolated_auth_dir: Path,
    ) -> None:
        assert resolve_api_key("LLM_API_KEY") == ""


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_six_builtin_providers_registered(self) -> None:
        names = list_providers()
        assert set(names) == {
            "anthropic", "openai", "zhipu",
            "nvidia_nim", "openrouter", "deepseek",
        }

    def test_unknown_provider_raises(self) -> None:
        with pytest.raises(UnknownProviderError):
            get_handler("does-not-exist")

    def test_handler_protocol_satisfied(self) -> None:
        # Every shipped handler has the Protocol methods.
        for name in list_providers():
            handler = get_handler(name)
            assert hasattr(handler, "login")
            assert hasattr(handler, "logout")
            assert hasattr(handler, "status")
            assert hasattr(handler, "credentials_for_request")
            assert handler.provider_name == name


# ---------------------------------------------------------------------------
# NIM free-tier note
# ---------------------------------------------------------------------------


class TestNvidiaFreeTier:
    def test_free_tier_note_when_nvapi_prefix(
        self, _isolated_auth_dir: Path,
    ) -> None:
        prompts = iter(["nvapi-AAAAAAAA-1234567890abcdef"])
        handler = NvidiaNimHandler(prompt=lambda msg, secret=False: next(prompts))
        handler.login()
        status = handler.status()
        assert "free-tier" in status.note

    def test_no_free_tier_note_for_other_keys(
        self, _isolated_auth_dir: Path,
    ) -> None:
        prompts = iter(["my-org-key-totally-different"])
        handler = NvidiaNimHandler(prompt=lambda msg, secret=False: next(prompts))
        handler.login()
        status = handler.status()
        assert "free-tier" not in status.note


# ---------------------------------------------------------------------------
# Credential leakage in logs
# ---------------------------------------------------------------------------


class TestNoCredentialLeak:
    def test_login_at_debug_level_does_not_log_full_key(
        self, _isolated_auth_dir: Path,
        caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        prompts = iter(["sk-do-not-leak-1234567890abcdef"])
        handler = OpenAIHandler(prompt=lambda msg, secret=False: next(prompts))
        with caplog.at_level(logging.DEBUG):
            handler.login()
        assert_no_credential_leak(
            caplog.text,
            secrets=("sk-do-not-leak-1234567890abcdef",),
        )

    def test_status_at_debug_level_does_not_log_full_key(
        self, _isolated_auth_dir: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        prompts = iter(["sk-no-leak-status-7890abcdef"])
        handler = OpenAIHandler(prompt=lambda msg, secret=False: next(prompts))
        handler.login()
        caplog.clear()
        with caplog.at_level(logging.DEBUG):
            for _ in range(3):
                handler.status()
        assert_no_credential_leak(
            caplog.text,
            secrets=("sk-no-leak-status-7890abcdef",),
        )


# ---------------------------------------------------------------------------
# Dispatcher /auth wiring
# ---------------------------------------------------------------------------


class TestDispatcherAuthCommand:
    @pytest.fixture()
    def dispatcher_setup(self, monkeypatch: pytest.MonkeyPatch) -> Any:
        from tests.fixtures.runtime import make_test_runtime  # type: ignore

        return make_test_runtime

    def _run_cmd(self, args: str, capture: list[str]) -> None:
        """Invoke the dispatcher's /auth handler with a captured view."""
        from llm_code.view.dispatcher import CommandDispatcher

        class _CapturingView:
            def print_info(self, text: str) -> None:
                capture.append(("info", text))

            def print_error(self, text: str) -> None:
                capture.append(("error", text))

            def request_exit(self) -> None:
                capture.append(("exit", ""))

        # We call _cmd_auth directly with a stub state/renderer; the
        # command doesn't touch the state for this surface.
        view = _CapturingView()

        class _Stub:
            pass

        dispatcher = CommandDispatcher.__new__(CommandDispatcher)
        dispatcher._view = view
        dispatcher._state = _Stub()
        dispatcher._renderer = _Stub()
        dispatcher._cmd_auth(args)

    def test_list_subcommand(self, _isolated_auth_dir: Path) -> None:
        captured: list[tuple[str, str]] = []
        self._run_cmd("list", captured)
        assert any("anthropic" in text for kind, text in captured if kind == "info")
        assert any("not-logged-in" in text for kind, text in captured if kind == "info")

    def test_status_subcommand(self, _isolated_auth_dir: Path) -> None:
        captured: list[tuple[str, str]] = []
        self._run_cmd("status", captured)
        assert any("OPENAI_API_KEY" in text for kind, text in captured)

    def test_login_unknown_provider_emits_error(
        self, _isolated_auth_dir: Path,
    ) -> None:
        captured: list[tuple[str, str]] = []
        self._run_cmd("login bogus", captured)
        kinds = [kind for kind, _ in captured]
        assert "error" in kinds

    def test_logout_unknown_provider_emits_error(
        self, _isolated_auth_dir: Path,
    ) -> None:
        captured: list[tuple[str, str]] = []
        self._run_cmd("logout bogus", captured)
        assert any(kind == "error" for kind, _ in captured)

    def test_no_args_shows_list(self, _isolated_auth_dir: Path) -> None:
        captured: list[tuple[str, str]] = []
        self._run_cmd("", captured)
        assert any("Provider auth status" in text for kind, text in captured)
