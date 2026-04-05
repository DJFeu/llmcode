"""Tests for llm_code.analysis.rules — core types + registry."""
from __future__ import annotations

from pathlib import Path

import pytest

from llm_code.analysis.rules import Violation, Rule, AnalysisResult, RuleRegistry


class TestViolation:
    def test_frozen(self) -> None:
        v = Violation(
            rule_key="bare-except",
            severity="high",
            file_path="src/api.py",
            line=88,
            message="Bare except clause",
        )
        assert v.rule_key == "bare-except"
        assert v.severity == "high"
        assert v.line == 88
        with pytest.raises(AttributeError):
            v.line = 99  # type: ignore[misc]

    def test_end_line_defaults_zero(self) -> None:
        v = Violation(
            rule_key="god-module",
            severity="medium",
            file_path="big.py",
            line=0,
            message="God module",
        )
        assert v.end_line == 0

    def test_to_dict_roundtrip(self) -> None:
        v = Violation(
            rule_key="todo-fixme",
            severity="low",
            file_path="foo.py",
            line=5,
            message="TODO: fix",
        )
        d = v.to_dict()
        assert d["rule_key"] == "todo-fixme"
        assert d["line"] == 5
        v2 = Violation.from_dict(d)
        assert v2 == v


class TestRule:
    def test_frozen(self) -> None:
        def _check(file_path: str, content: str, tree: object = None) -> list[Violation]:
            return []

        r = Rule(
            key="bare-except",
            name="Bare except clause",
            severity="high",
            languages=("python",),
            check=_check,
        )
        assert r.key == "bare-except"
        assert r.languages == ("python",)

    def test_check_callable(self) -> None:
        def _check(file_path: str, content: str, tree: object = None) -> list[Violation]:
            return [Violation(
                rule_key="test", severity="low",
                file_path=file_path, line=1, message="test",
            )]

        r = Rule(
            key="test", name="Test", severity="low",
            languages=("*",), check=_check,
        )
        results = r.check("foo.py", "content")
        assert len(results) == 1


class TestAnalysisResult:
    def test_frozen(self) -> None:
        result = AnalysisResult(violations=(), file_count=10, duration_ms=42.5)
        assert result.file_count == 10
        assert result.duration_ms == 42.5

    def test_summary_counts(self) -> None:
        violations = (
            Violation(rule_key="a", severity="critical", file_path="x.py", line=1, message="m"),
            Violation(rule_key="b", severity="high", file_path="x.py", line=2, message="m"),
            Violation(rule_key="c", severity="high", file_path="y.py", line=3, message="m"),
            Violation(rule_key="d", severity="low", file_path="z.py", line=4, message="m"),
        )
        result = AnalysisResult(violations=violations, file_count=3, duration_ms=10.0)
        counts = result.summary_counts()
        assert counts == {"critical": 1, "high": 2, "medium": 0, "low": 1}


class TestRuleRegistry:
    def test_register_and_get(self) -> None:
        registry = RuleRegistry()
        assert len(registry.all_rules()) == 0

        def _check(file_path: str, content: str, tree: object = None) -> list[Violation]:
            return []

        rule = Rule(
            key="test-rule", name="Test", severity="low",
            languages=("python",), check=_check,
        )
        registry.register(rule)
        assert len(registry.all_rules()) == 1
        assert registry.get("test-rule") == rule

    def test_get_missing_returns_none(self) -> None:
        registry = RuleRegistry()
        assert registry.get("nonexistent") is None

    def test_rules_for_language(self) -> None:
        registry = RuleRegistry()

        def _noop(fp: str, c: str, t: object = None) -> list[Violation]:
            return []

        registry.register(Rule(key="py-only", name="Py", severity="low", languages=("python",), check=_noop))
        registry.register(Rule(key="js-only", name="Js", severity="low", languages=("javascript",), check=_noop))
        registry.register(Rule(key="universal", name="Uni", severity="low", languages=("*",), check=_noop))

        py_rules = registry.rules_for_language("python")
        assert {r.key for r in py_rules} == {"py-only", "universal"}

        js_rules = registry.rules_for_language("javascript")
        assert {r.key for r in js_rules} == {"js-only", "universal"}

    def test_duplicate_key_raises(self) -> None:
        registry = RuleRegistry()
        def _noop(fp: str, c: str, t: object = None) -> list[Violation]:
            return []
        rule = Rule(key="dup", name="Dup", severity="low", languages=("*",), check=_noop)
        registry.register(rule)
        with pytest.raises(ValueError, match="already registered"):
            registry.register(rule)
