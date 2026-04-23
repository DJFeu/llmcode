"""Canonical hayhooks error types with HTTP status mapping.

All errors serialise to the OpenAI-compatible envelope:

    {"error": {"message": "...", "type": "...", "code": "..."}}
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class HayhooksError(Exception):
    """Base class for hayhooks errors — carries OpenAI-shape metadata."""

    message: str
    error_type: str = "server_error"
    code: str = "internal_error"
    http_status: int = 500

    def __post_init__(self) -> None:  # pragma: no cover — trivial
        # Make the exception message human-readable
        super().__init__(self.message)

    def to_envelope(self) -> dict:
        """Return OpenAI-compatible error envelope."""
        return {
            "error": {
                "message": self.message,
                "type": self.error_type,
                "code": self.code,
            }
        }


class MissingTokenError(HayhooksError):
    def __init__(self, message: str = "missing bearer token") -> None:
        super().__init__(
            message=message,
            error_type="authentication_error",
            code="missing_token",
            http_status=401,
        )


class InvalidTokenError(HayhooksError):
    def __init__(self, message: str = "invalid bearer token") -> None:
        super().__init__(
            message=message,
            error_type="authentication_error",
            code="invalid_token",
            http_status=401,
        )


class PayloadTooLargeError(HayhooksError):
    def __init__(self, message: str = "payload exceeds size limit") -> None:
        super().__init__(
            message=message,
            error_type="invalid_request_error",
            code="payload_too_large",
            http_status=413,
        )


class RateLimitError(HayhooksError):
    # ``retry_after`` is carried as a real dataclass field (not an
    # ad-hoc attribute) so ``frozen=True`` on the base class doesn't
    # fight us when __post_init__ wants to stash it.
    retry_after: float | None = None

    def __init__(
        self,
        message: str = "rate limit exceeded",
        *,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(
            message=message,
            error_type="rate_limit_error",
            code="rate_limit_exceeded",
            http_status=429,
        )
        # frozen=True on the base blocks normal setattr; use object.__setattr__
        # so the optional hint lands on the instance.
        object.__setattr__(self, "retry_after", retry_after)


class BadRequestError(HayhooksError):
    def __init__(self, message: str = "bad request") -> None:
        super().__init__(
            message=message,
            error_type="invalid_request_error",
            code="bad_request",
            http_status=400,
        )


class RemoteBindRefusedError(HayhooksError):
    def __init__(self, message: str = "remote bind refused") -> None:
        super().__init__(
            message=message,
            error_type="configuration_error",
            code="remote_bind_refused",
            http_status=400,
        )


STATUS_TO_TYPE: dict[int, str] = {
    400: "invalid_request_error",
    401: "authentication_error",
    403: "permission_denied",
    404: "not_found",
    413: "invalid_request_error",
    429: "rate_limit_error",
    500: "server_error",
    503: "service_unavailable",
}


def envelope_from_exc(exc: Exception, fallback_status: int = 500) -> tuple[int, dict]:
    """Convert any exception to ``(status, envelope)`` tuple."""
    if isinstance(exc, HayhooksError):
        return exc.http_status, exc.to_envelope()
    return fallback_status, {
        "error": {
            "message": str(exc),
            "type": STATUS_TO_TYPE.get(fallback_status, "server_error"),
            "code": "internal_error",
        }
    }
