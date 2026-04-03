"""Tests for llm_code.utils.errors — friendly_error and suggest_fix."""
from __future__ import annotations

import json
import subprocess


from llm_code.utils.errors import friendly_error, suggest_fix


# ---------------------------------------------------------------------------
# friendly_error
# ---------------------------------------------------------------------------


class TestFriendlyError:
    def test_file_not_found_includes_path(self):
        exc = FileNotFoundError(2, "No such file or directory")
        exc.filename = "/tmp/missing.txt"
        msg = friendly_error(exc)
        assert "/tmp/missing.txt" in msg
        assert "File not found" in msg
        assert "/cd" in msg

    def test_file_not_found_context_prefix(self):
        exc = FileNotFoundError(2, "No such file or directory")
        exc.filename = "/tmp/x.txt"
        msg = friendly_error(exc, context="read_file")
        assert msg.startswith("[read_file]")

    def test_permission_error_message(self):
        exc = PermissionError(13, "Permission denied")
        exc.filename = "/etc/shadow"
        msg = friendly_error(exc)
        assert "Permission denied" in msg
        assert "/etc/shadow" in msg
        assert "read-only" in msg

    def test_json_decode_error(self):
        try:
            json.loads("{invalid")
        except json.JSONDecodeError as exc:
            msg = friendly_error(exc)
            assert "Invalid JSON" in msg
            assert str(exc.lineno) in msg

    def test_timeout_expired(self):
        exc = subprocess.TimeoutExpired(cmd="sleep 100", timeout=5)
        msg = friendly_error(exc)
        assert "timed out" in msg.lower()
        assert "5" in msg  # timeout value

    def test_connection_error(self):
        exc = ConnectionError("Connection refused")
        msg = friendly_error(exc)
        assert "Connection failed" in msg
        assert "server is running" in msg

    def test_generic_exception_fallback(self):
        exc = ValueError("something went wrong")
        msg = friendly_error(exc)
        assert "ValueError" in msg
        assert "something went wrong" in msg

    def test_no_context_no_prefix(self):
        exc = ValueError("oops")
        msg = friendly_error(exc)
        assert not msg.startswith("[")

    def test_empty_context_no_prefix(self):
        exc = ValueError("oops")
        msg = friendly_error(exc, context="")
        assert not msg.startswith("[")


# ---------------------------------------------------------------------------
# suggest_fix
# ---------------------------------------------------------------------------


class TestSuggestFix:
    def test_file_not_found_suggestion(self):
        exc = FileNotFoundError(2, "No such file or directory")
        exc.filename = "/tmp/x.txt"
        suggestion = suggest_fix(exc)
        assert suggestion is not None
        assert "cd" in suggestion.lower() or "path" in suggestion.lower()

    def test_permission_error_suggestion(self):
        exc = PermissionError(13, "Permission denied")
        exc.filename = "/etc/shadow"
        suggestion = suggest_fix(exc)
        assert suggestion is not None
        assert "permission" in suggestion.lower() or "ls" in suggestion.lower()

    def test_json_decode_error_suggestion(self):
        try:
            json.loads("{bad}")
        except json.JSONDecodeError as exc:
            suggestion = suggest_fix(exc)
            assert suggestion is not None
            assert str(exc.lineno) in suggestion

    def test_timeout_expired_suggestion(self):
        exc = subprocess.TimeoutExpired(cmd="sleep 100", timeout=30)
        suggestion = suggest_fix(exc)
        assert suggestion is not None
        assert "timeout" in suggestion.lower()

    def test_connection_error_suggestion(self):
        exc = ConnectionError("refused")
        suggestion = suggest_fix(exc)
        assert suggestion is not None
        assert "port" in suggestion.lower() or "server" in suggestion.lower()

    def test_generic_exception_returns_none(self):
        exc = RuntimeError("unknown")
        suggestion = suggest_fix(exc)
        assert suggestion is None
