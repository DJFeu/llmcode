"""Tests for ``llm_code.hayhooks.errors``."""
from __future__ import annotations

import pytest

from llm_code.hayhooks.errors import (
    BadRequestError,
    HayhooksError,
    InvalidTokenError,
    MissingTokenError,
    PayloadTooLargeError,
    RateLimitError,
    RemoteBindRefusedError,
    envelope_from_exc,
)


class TestEnvelopes:
    @pytest.mark.parametrize(
        "cls,status",
        [
            (MissingTokenError, 401),
            (InvalidTokenError, 401),
            (PayloadTooLargeError, 413),
            (RateLimitError, 429),
            (BadRequestError, 400),
            (RemoteBindRefusedError, 400),
        ],
    )
    def test_error_carries_http_status(self, cls, status):
        exc = cls()
        assert exc.http_status == status

    def test_envelope_shape(self):
        exc = MissingTokenError("oops")
        env = exc.to_envelope()
        assert set(env["error"].keys()) == {"message", "type", "code"}
        assert env["error"]["message"] == "oops"

    def test_envelope_from_hayhooks_error(self):
        exc = RateLimitError("slow down")
        status, body = envelope_from_exc(exc)
        assert status == 429
        assert body["error"]["code"] == "rate_limit_exceeded"

    def test_envelope_from_generic_exception(self):
        status, body = envelope_from_exc(ValueError("nope"))
        assert status == 500
        assert body["error"]["message"] == "nope"

    def test_is_exception(self):
        assert issubclass(HayhooksError, Exception)
        assert str(HayhooksError(message="x")) == "x"
