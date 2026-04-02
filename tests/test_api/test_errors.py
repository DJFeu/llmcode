"""Tests for llm_code.api.errors — TDD: written before implementation."""
import pytest

from llm_code.api.errors import (
    LLMCodeError,
    ProviderError,
    ProviderConnectionError,
    ProviderAuthError,
    ProviderRateLimitError,
    ProviderModelNotFoundError,
    ToolError,
    ToolNotFoundError,
    ToolPermissionDenied,
    ToolExecutionError,
    ConfigError,
    SessionError,
)


# ---------------------------------------------------------------------------
# Hierarchy
# ---------------------------------------------------------------------------

class TestHierarchy:
    def test_provider_error_is_llm_code_error(self):
        assert issubclass(ProviderError, LLMCodeError)

    def test_provider_connection_error_is_provider_error(self):
        assert issubclass(ProviderConnectionError, ProviderError)

    def test_provider_auth_error_is_provider_error(self):
        assert issubclass(ProviderAuthError, ProviderError)

    def test_provider_rate_limit_error_is_provider_error(self):
        assert issubclass(ProviderRateLimitError, ProviderError)

    def test_provider_model_not_found_is_provider_error(self):
        assert issubclass(ProviderModelNotFoundError, ProviderError)

    def test_tool_error_is_llm_code_error(self):
        assert issubclass(ToolError, LLMCodeError)

    def test_tool_not_found_is_tool_error(self):
        assert issubclass(ToolNotFoundError, ToolError)

    def test_tool_permission_denied_is_tool_error(self):
        assert issubclass(ToolPermissionDenied, ToolError)

    def test_tool_execution_error_is_tool_error(self):
        assert issubclass(ToolExecutionError, ToolError)

    def test_config_error_is_llm_code_error(self):
        assert issubclass(ConfigError, LLMCodeError)

    def test_session_error_is_llm_code_error(self):
        assert issubclass(SessionError, LLMCodeError)

    def test_all_catchable_as_llm_code_error(self):
        errors = [
            ProviderConnectionError("conn"),
            ProviderAuthError("auth"),
            ProviderRateLimitError("rate"),
            ProviderModelNotFoundError("model"),
            ToolNotFoundError("tool"),
            ToolPermissionDenied("perm"),
            ToolExecutionError("exec"),
            ConfigError("cfg"),
            SessionError("sess"),
        ]
        for err in errors:
            assert isinstance(err, LLMCodeError)


# ---------------------------------------------------------------------------
# is_retryable
# ---------------------------------------------------------------------------

class TestIsRetryable:
    def test_provider_error_default_not_retryable(self):
        err = ProviderError("base")
        assert err.is_retryable is False

    def test_provider_connection_error_is_retryable(self):
        err = ProviderConnectionError("conn refused")
        assert err.is_retryable is True

    def test_provider_auth_error_not_retryable(self):
        err = ProviderAuthError("bad key")
        assert err.is_retryable is False

    def test_provider_rate_limit_error_is_retryable(self):
        err = ProviderRateLimitError("too many")
        assert err.is_retryable is True

    def test_provider_model_not_found_not_retryable(self):
        err = ProviderModelNotFoundError("no such model")
        assert err.is_retryable is False


# ---------------------------------------------------------------------------
# Error messages
# ---------------------------------------------------------------------------

class TestErrorMessages:
    def test_llm_code_error_message(self):
        err = LLMCodeError("something failed")
        assert "something failed" in str(err)

    def test_provider_connection_error_message(self):
        err = ProviderConnectionError("connection refused")
        assert "connection refused" in str(err)

    def test_tool_not_found_message(self):
        err = ToolNotFoundError("bash")
        assert "bash" in str(err)

    def test_tool_execution_error_message(self):
        err = ToolExecutionError("exit code 1")
        assert "exit code 1" in str(err)

    def test_config_error_message(self):
        err = ConfigError("missing api_key")
        assert "missing api_key" in str(err)

    def test_session_error_message(self):
        err = SessionError("session expired")
        assert "session expired" in str(err)


# ---------------------------------------------------------------------------
# Raiseable / catchable
# ---------------------------------------------------------------------------

class TestRaiseable:
    def test_raise_provider_connection_error(self):
        with pytest.raises(ProviderConnectionError):
            raise ProviderConnectionError("no route")

    def test_catch_as_provider_error(self):
        with pytest.raises(ProviderError):
            raise ProviderConnectionError("no route")

    def test_catch_as_llm_code_error(self):
        with pytest.raises(LLMCodeError):
            raise ProviderRateLimitError("slow down")

    def test_raise_tool_execution_error(self):
        with pytest.raises(ToolExecutionError):
            raise ToolExecutionError("bad exit")

    def test_catch_tool_error_as_llm_code_error(self):
        with pytest.raises(LLMCodeError):
            raise ToolNotFoundError("no tool")
