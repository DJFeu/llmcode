"""Tests for the unified error model (H6 — Sprint 3)."""
from __future__ import annotations

import json

import pytest

from llm_code.error_model import (
    ErrorSeverity,
    LLMCodeError,
    SourceLocation,
)


class TestErrorSeverity:
    def test_enum_values(self) -> None:
        assert ErrorSeverity.INFO.value == "info"
        assert ErrorSeverity.WARNING.value == "warning"
        assert ErrorSeverity.ERROR.value == "error"
        assert ErrorSeverity.FATAL.value == "fatal"

    def test_ordered(self) -> None:
        """INFO < WARNING < ERROR < FATAL so callers can filter by minimum."""
        assert ErrorSeverity.INFO < ErrorSeverity.WARNING
        assert ErrorSeverity.WARNING < ErrorSeverity.ERROR
        assert ErrorSeverity.ERROR < ErrorSeverity.FATAL


class TestSourceLocation:
    def test_frozen(self) -> None:
        loc = SourceLocation(file_path="/tmp/a.py", line=1)
        with pytest.raises(Exception):
            loc.line = 2  # type: ignore[misc]

    def test_minimal_construction(self) -> None:
        loc = SourceLocation(file_path="/tmp/a.py")
        assert loc.line is None
        assert loc.column is None
        assert loc.line_text == ""

    def test_full_construction(self) -> None:
        loc = SourceLocation(
            file_path="/tmp/a.py", line=42, column=7,
            line_text="    x = 1",
        )
        assert loc.file_path == "/tmp/a.py"
        assert loc.line == 42
        assert loc.column == 7
        assert "x = 1" in loc.line_text

    def test_format_human_readable(self) -> None:
        loc = SourceLocation(file_path="/tmp/a.py", line=42, column=7)
        s = loc.format()
        assert "/tmp/a.py" in s
        assert "42" in s
        assert "7" in s

    def test_format_without_line_still_useful(self) -> None:
        loc = SourceLocation(file_path="/tmp/a.py")
        s = loc.format()
        assert s == "/tmp/a.py"


class TestLLMCodeError:
    def test_is_exception(self) -> None:
        err = LLMCodeError(code="TEST", message="x")
        assert isinstance(err, Exception)

    def test_raises_cleanly(self) -> None:
        with pytest.raises(LLMCodeError) as excinfo:
            raise LLMCodeError(code="E_FAKE", message="boom")
        assert excinfo.value.code == "E_FAKE"
        assert "boom" in str(excinfo.value)

    def test_severity_default_is_error(self) -> None:
        err = LLMCodeError(code="X", message="x")
        assert err.severity is ErrorSeverity.ERROR

    def test_location_default_is_none(self) -> None:
        err = LLMCodeError(code="X", message="x")
        assert err.location is None

    def test_context_default_empty(self) -> None:
        err = LLMCodeError(code="X", message="x")
        assert err.context == {}

    def test_with_location_returns_new_instance(self) -> None:
        """Error is logically frozen — ``with_location`` must not mutate."""
        err = LLMCodeError(code="X", message="x")
        loc = SourceLocation(file_path="/tmp/a.py", line=10)
        err2 = err.with_location(loc)
        assert err2 is not err
        assert err2.location == loc
        assert err.location is None  # original untouched

    def test_with_location_preserves_other_fields(self) -> None:
        err = LLMCodeError(
            code="X", message="m", severity=ErrorSeverity.FATAL,
            context={"k": 1},
        )
        loc = SourceLocation(file_path="/a", line=5)
        err2 = err.with_location(loc)
        assert err2.code == "X"
        assert err2.message == "m"
        assert err2.severity is ErrorSeverity.FATAL
        assert err2.context == {"k": 1}

    def test_with_context_merges(self) -> None:
        err = LLMCodeError(code="X", message="x", context={"a": 1})
        err2 = err.with_context(b=2, c=3)
        assert err2.context == {"a": 1, "b": 2, "c": 3}
        assert err.context == {"a": 1}  # original untouched

    def test_to_dict_json_safe(self) -> None:
        err = LLMCodeError(
            code="E_PATCH_FAIL",
            message="patch did not apply",
            severity=ErrorSeverity.ERROR,
            location=SourceLocation(file_path="/tmp/a.py", line=12, column=3),
            context={"hunk_index": 2},
        )
        data = err.to_dict()
        json.dumps(data)  # must not raise
        assert data["code"] == "E_PATCH_FAIL"
        assert data["severity"] == "error"
        assert data["location"]["file_path"] == "/tmp/a.py"
        assert data["context"] == {"hunk_index": 2}


class TestChainedConstruction:
    def test_fluent_style(self) -> None:
        err = (
            LLMCodeError(code="E_SANDBOX_DENIED", message="bash blocked")
            .with_location(SourceLocation(file_path="script.sh", line=3))
            .with_context(sandbox="docker", rule="no-network")
        )
        assert err.code == "E_SANDBOX_DENIED"
        assert err.location.file_path == "script.sh"
        assert err.context["rule"] == "no-network"

    def test_fluent_chain_never_mutates_base(self) -> None:
        base = LLMCodeError(code="X", message="m")
        chained = base.with_location(
            SourceLocation(file_path="/a")
        ).with_context(k=1)
        assert base.location is None
        assert base.context == {}
        assert chained.location is not None
        assert chained.context == {"k": 1}
