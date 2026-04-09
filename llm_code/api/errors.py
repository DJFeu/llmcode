"""Exception hierarchy for the llm-code API layer."""
from __future__ import annotations


class LLMCodeError(Exception):
    """Base exception for all llm-code errors."""


class ProviderError(LLMCodeError):
    """Error returned by or related to an LLM provider."""

    def __init__(self, message: str, *, is_retryable: bool = False) -> None:
        super().__init__(message)
        self.is_retryable = is_retryable


class ProviderConnectionError(ProviderError):
    """Network-level failure connecting to the provider (retryable)."""

    def __init__(self, message: str) -> None:
        super().__init__(message, is_retryable=True)


class ProviderAuthError(ProviderError):
    """Authentication / authorisation failure (not retryable)."""

    def __init__(self, message: str) -> None:
        super().__init__(message, is_retryable=False)


class ProviderRateLimitError(ProviderError):
    """Provider rate-limit exceeded (retryable).

    Wave2-1b: carries an optional ``retry_after`` hint in seconds
    when the provider sends an HTTP Retry-After header on the 429
    response. Callers should honor this value instead of their own
    exponential backoff when it is non-None, capped at a reasonable
    maximum to avoid wedging on a misbehaving proxy.
    """

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message, is_retryable=True)
        self.retry_after = retry_after


class ProviderTimeoutError(ProviderError):
    """Network timeout talking to the provider (retryable).

    Wave2-1b: distinct from ProviderConnectionError so the retry path
    can use a timeout-specific exponential backoff. Previously
    ``httpx.ReadTimeout`` and ``httpx.ConnectTimeout`` fell through
    _post_with_retry uncaught and became generic ``Exception`` in the
    conversation loop, skipping the retry budget entirely.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message, is_retryable=True)


class ProviderModelNotFoundError(ProviderError):
    """Requested model does not exist on the provider (not retryable)."""

    def __init__(self, message: str) -> None:
        super().__init__(message, is_retryable=False)


class ProviderOverloadError(ProviderError):
    """Provider is overloaded (HTTP 529); retryable with long backoff."""

    def __init__(self, message: str) -> None:
        super().__init__(message, is_retryable=True)


class ToolError(LLMCodeError):
    """Base exception for tool-related errors."""


class ToolNotFoundError(ToolError):
    """A tool referenced by name does not exist in the registry."""


class ToolPermissionDenied(ToolError):
    """The tool is not permitted under the current permission policy."""


class ToolExecutionError(ToolError):
    """A tool raised an error during execution."""


class ConfigError(LLMCodeError):
    """Invalid or missing configuration."""


class SessionError(LLMCodeError):
    """Error related to conversation session state."""
