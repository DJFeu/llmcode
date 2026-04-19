"""Tests for from_provider_exception (S4.4).

Bridges the existing provider-side exception hierarchy
(``llm_code/api/errors.py``) onto the structured surface-layer
``LLMCodeError`` so audit / SDK consumers marshal transport failures
through the same envelope as tool failures.
"""
from __future__ import annotations

from llm_code.api.errors import (
    ProviderAuthError,
    ProviderConnectionError,
    ProviderModelNotFoundError,
    ProviderOverloadError,
    ProviderRateLimitError,
    ProviderTimeoutError,
)
from llm_code.error_model import LLMCodeError, from_provider_exception


class TestCodeMapping:
    def test_auth_error(self) -> None:
        err = from_provider_exception(ProviderAuthError("bad key"))
        assert isinstance(err, LLMCodeError)
        assert err.code == "E_PROVIDER_AUTH"

    def test_model_not_found(self) -> None:
        err = from_provider_exception(ProviderModelNotFoundError("qwen-9"))
        assert err.code == "E_PROVIDER_MODEL_NOT_FOUND"

    def test_rate_limit(self) -> None:
        err = from_provider_exception(
            ProviderRateLimitError("429", retry_after=12.0),
        )
        assert err.code == "E_PROVIDER_RATE_LIMIT"

    def test_overload(self) -> None:
        err = from_provider_exception(ProviderOverloadError("529"))
        assert err.code == "E_PROVIDER_OVERLOAD"

    def test_timeout(self) -> None:
        err = from_provider_exception(ProviderTimeoutError("read timeout"))
        assert err.code == "E_PROVIDER_TIMEOUT"

    def test_connection(self) -> None:
        err = from_provider_exception(ProviderConnectionError("dns failure"))
        assert err.code == "E_PROVIDER_CONNECTION"

    def test_unknown_exception_falls_back(self) -> None:
        """Callers occasionally hand non-ProviderError exceptions here
        (e.g. a bare ``RuntimeError`` from a third-party SDK). Keep
        the helper robust."""
        err = from_provider_exception(RuntimeError("oops"))
        assert err.code == "E_PROVIDER_UNKNOWN"


class TestMessageAndContext:
    def test_message_is_exception_str(self) -> None:
        err = from_provider_exception(ProviderAuthError("bad key"))
        assert err.message == "bad key"

    def test_context_has_exception_type(self) -> None:
        err = from_provider_exception(ProviderAuthError("x"))
        assert err.context["exception_type"] == "ProviderAuthError"

    def test_context_embeds_retry_after(self) -> None:
        err = from_provider_exception(
            ProviderRateLimitError("429", retry_after=15.0),
        )
        assert err.context["retry_after"] == 15.0

    def test_context_embeds_is_retryable_when_available(self) -> None:
        err = from_provider_exception(ProviderConnectionError("x"))
        assert err.context["is_retryable"] is True

    def test_context_respects_model_and_base_url(self) -> None:
        err = from_provider_exception(
            ProviderTimeoutError("timeout"),
            base_url="https://api.example.com",
            model="qwen3.6-plus",
        )
        assert err.context["base_url"] == "https://api.example.com"
        assert err.context["model"] == "qwen3.6-plus"

    def test_extra_context_merged(self) -> None:
        err = from_provider_exception(
            ProviderTimeoutError("timeout"),
            model="x",
            attempt=3,
            request_kind="foreground",
        )
        assert err.context["attempt"] == 3
        assert err.context["request_kind"] == "foreground"

    def test_none_context_values_filtered(self) -> None:
        """Avoid polluting the context dict with empty / None fields —
        they're noise in the JSON wire format."""
        err = from_provider_exception(ProviderAuthError("x"))
        assert "base_url" not in err.context  # empty default dropped
        assert "retry_after" not in err.context  # None dropped


class TestSeverity:
    def test_permanent_error_severity_fatal(self) -> None:
        from llm_code.error_model import ErrorSeverity

        err = from_provider_exception(ProviderAuthError("x"))
        # Auth failure won't self-heal on retry — fatal so callers
        # propagate the failure rather than silently retrying.
        assert err.severity is ErrorSeverity.FATAL

    def test_retryable_error_severity_error(self) -> None:
        from llm_code.error_model import ErrorSeverity

        err = from_provider_exception(ProviderRateLimitError("429"))
        assert err.severity is ErrorSeverity.ERROR
