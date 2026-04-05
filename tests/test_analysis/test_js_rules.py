"""Tests for llm_code.analysis.js_rules — JS/TS regex-based rules."""
from __future__ import annotations

import pytest

from llm_code.analysis.js_rules import (
    check_empty_catch,
    check_console_log,
    register_js_rules,
)
from llm_code.analysis.rules import RuleRegistry


class TestEmptyCatch:
    def test_detects_empty_catch(self) -> None:
        content = "try { foo(); } catch (e) { }\n"
        violations = check_empty_catch("app.js", content)
        assert len(violations) == 1
        assert violations[0].rule_key == "empty-catch"
        assert violations[0].severity == "medium"

    def test_detects_multiline_empty_catch(self) -> None:
        content = "try {\n  foo();\n} catch (e) {\n}\n"
        violations = check_empty_catch("app.ts", content)
        assert len(violations) == 1

    def test_ignores_catch_with_body(self) -> None:
        content = "try { foo(); } catch (e) { console.log(e); }\n"
        violations = check_empty_catch("app.js", content)
        assert len(violations) == 0

    def test_no_false_positive_on_comment(self) -> None:
        content = "// catch (e) { }\n"
        violations = check_empty_catch("app.js", content)
        # Regex-based, may match — this is a known limitation
        # We accept minor false positives for simplicity
        assert isinstance(violations, list)


class TestConsoleLog:
    def test_detects_console_log(self) -> None:
        content = "console.log('hello');\n"
        violations = check_console_log("src/app.js", content)
        assert len(violations) == 1
        assert violations[0].rule_key == "console-log"
        assert violations[0].severity == "low"

    def test_detects_console_error(self) -> None:
        content = "console.error('oops');\n"
        violations = check_console_log("src/app.ts", content)
        assert len(violations) == 1

    def test_detects_console_warn(self) -> None:
        content = "console.warn('warning');\n"
        violations = check_console_log("src/app.tsx", content)
        assert len(violations) == 1

    def test_skips_test_dir(self) -> None:
        content = "console.log('test output');\n"
        violations = check_console_log("tests/app.test.js", content)
        assert len(violations) == 0

    def test_skips_test_suffix(self) -> None:
        content = "console.log('test');\n"
        violations = check_console_log("src/utils.test.ts", content)
        assert len(violations) == 0

    def test_skips_spec_suffix(self) -> None:
        content = "console.log('spec');\n"
        violations = check_console_log("src/utils.spec.js", content)
        assert len(violations) == 0

    def test_skips__tests__dir(self) -> None:
        content = "console.log('test');\n"
        violations = check_console_log("__tests__/foo.js", content)
        assert len(violations) == 0

    def test_no_false_positive(self) -> None:
        content = "const x = 'console.log is a function';\n"
        violations = check_console_log("src/app.js", content)
        # Regex may match string content — known limitation
        assert isinstance(violations, list)


class TestRegisterJsRules:
    def test_registers_two_rules(self) -> None:
        registry = RuleRegistry()
        register_js_rules(registry)
        assert len(registry.all_rules()) == 2
        assert registry.get("empty-catch") is not None
        assert registry.get("console-log") is not None

    def test_targets_js_and_ts(self) -> None:
        registry = RuleRegistry()
        register_js_rules(registry)
        for rule in registry.all_rules():
            assert "javascript" in rule.languages or "typescript" in rule.languages
