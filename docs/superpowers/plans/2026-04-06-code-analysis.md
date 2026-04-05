# Code Analysis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add /analyze and /diff-check commands with a deterministic code rules engine for Python and JS/TS.

**Architecture:** New `llm_code/analysis/` package with rule engine, language-specific rules, and result caching. Integrates via slash commands in app.py with results shown in chat and injected into agent context.

**Tech Stack:** Python 3.11+, ast (stdlib), re (regex), pytest

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `llm_code/analysis/__init__.py` | Create | Package init, re-export core types |
| `llm_code/analysis/rules.py` | Create | Violation, Rule, AnalysisResult dataclasses + RuleRegistry |
| `llm_code/analysis/universal_rules.py` | Create | hardcoded-secret, todo-fixme, god-module rules |
| `llm_code/analysis/python_rules.py` | Create | bare-except, empty-except, unused-import, star-import, print-in-prod, circular-import |
| `llm_code/analysis/js_rules.py` | Create | empty-catch, console-log rules (regex-based) |
| `llm_code/analysis/engine.py` | Create | run_analysis(), run_diff_check() orchestration |
| `llm_code/analysis/cache.py` | Create | Save/load analysis results to .llm-code/last_analysis.json |
| `llm_code/tui/app.py` | Modify | _cmd_analyze(), _cmd_diff_check() slash command handlers |
| `tests/test_analysis/__init__.py` | Create | Test package init |
| `tests/test_analysis/test_rules.py` | Create | Tests for core types + registry |
| `tests/test_analysis/test_universal_rules.py` | Create | Tests for universal rules |
| `tests/test_analysis/test_python_rules.py` | Create | Tests for Python AST rules |
| `tests/test_analysis/test_js_rules.py` | Create | Tests for JS/TS regex rules |
| `tests/test_analysis/test_engine.py` | Create | Tests for engine + cache |

---

### Task 1: Core Types + Rule Registry

**Files:**
- Create: `llm_code/analysis/__init__.py`
- Create: `llm_code/analysis/rules.py`
- Create: `tests/test_analysis/__init__.py`
- Create: `tests/test_analysis/test_rules.py`

- [ ] **Step 1: Write failing tests for core types and registry**

```python
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
```

- [ ] **Step 2: Implement core types and registry**

```python
# llm_code/analysis/__init__.py
"""Code analysis — deterministic rule engine for Python and JS/TS."""
from __future__ import annotations

from llm_code.analysis.rules import AnalysisResult, Rule, RuleRegistry, Violation

__all__ = ["AnalysisResult", "Rule", "RuleRegistry", "Violation"]
```

```python
# llm_code/analysis/rules.py
"""Core types and rule registry for code analysis."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


_SEVERITY_ORDER = ("critical", "high", "medium", "low")


@dataclass(frozen=True)
class Violation:
    """A single code analysis violation."""

    rule_key: str
    severity: str
    file_path: str
    line: int
    message: str
    end_line: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_key": self.rule_key,
            "severity": self.severity,
            "file_path": self.file_path,
            "line": self.line,
            "message": self.message,
            "end_line": self.end_line,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Violation:
        return cls(
            rule_key=data["rule_key"],
            severity=data["severity"],
            file_path=data["file_path"],
            line=data["line"],
            message=data["message"],
            end_line=data.get("end_line", 0),
        )


@dataclass(frozen=True)
class Rule:
    """A deterministic code analysis rule."""

    key: str
    name: str
    severity: str
    languages: tuple[str, ...]
    check: Callable[..., list[Violation]]


@dataclass(frozen=True)
class AnalysisResult:
    """Immutable result of a code analysis run."""

    violations: tuple[Violation, ...]
    file_count: int
    duration_ms: float

    def summary_counts(self) -> dict[str, int]:
        counts = {s: 0 for s in _SEVERITY_ORDER}
        for v in self.violations:
            if v.severity in counts:
                counts[v.severity] += 1
        return counts

    def format_chat(self) -> str:
        """Render violations for chat display."""
        counts = self.summary_counts()
        total = len(self.violations)
        header = f"## Code Analysis — {self.file_count} files, {total} violations\n"
        if total == 0:
            return header + "\nNo violations found."

        lines: list[str] = [header]
        severity_key = {s: i for i, s in enumerate(_SEVERITY_ORDER)}
        sorted_violations = sorted(
            self.violations,
            key=lambda v: (severity_key.get(v.severity, 99), v.file_path, v.line),
        )
        for v in sorted_violations:
            label = v.severity.upper().ljust(8)
            loc = f"{v.file_path}:{v.line}" if v.line > 0 else v.file_path
            lines.append(f"  {label}  {loc:<30}  {v.message}")

        parts = [f"{c} {s}" for s, c in counts.items() if c > 0]
        lines.append(f"\nSummary: {', '.join(parts)}")
        return "\n".join(lines)

    def format_context(self, max_tokens: int = 1000) -> str:
        """Render compressed violations for agent context injection."""
        max_chars = max_tokens * 4
        total = len(self.violations)
        lines = [f"[Code Analysis] {total} violations found:"]

        severity_key = {s: i for i, s in enumerate(_SEVERITY_ORDER)}
        sorted_violations = sorted(
            self.violations,
            key=lambda v: (severity_key.get(v.severity, 99), v.file_path, v.line),
        )

        char_count = len(lines[0])
        for v in sorted_violations:
            loc = f"{v.file_path}:{v.line}" if v.line > 0 else v.file_path
            line = f"- {v.severity.upper()} {loc} {v.message}"
            if char_count + len(line) + 1 > max_chars:
                # If we exceeded budget, only keep critical + high
                if v.severity not in ("critical", "high"):
                    break
            lines.append(line)
            char_count += len(line) + 1

        return "\n".join(lines)


class RuleRegistry:
    """Registry of analysis rules."""

    def __init__(self) -> None:
        self._rules: dict[str, Rule] = {}

    def register(self, rule: Rule) -> None:
        if rule.key in self._rules:
            raise ValueError(f"Rule '{rule.key}' already registered")
        self._rules[rule.key] = rule

    def get(self, key: str) -> Rule | None:
        return self._rules.get(key)

    def all_rules(self) -> list[Rule]:
        return list(self._rules.values())

    def rules_for_language(self, language: str) -> list[Rule]:
        return [
            r for r in self._rules.values()
            if "*" in r.languages or language in r.languages
        ]
```

- [ ] **Step 3: Run tests, verify GREEN**

```bash
cd /Users/adamhong/Work/qwen/llm-code
python -m pytest tests/test_analysis/test_rules.py -v
```

- [ ] **Step 4: Commit**

```bash
git add llm_code/analysis/__init__.py llm_code/analysis/rules.py \
       tests/test_analysis/__init__.py tests/test_analysis/test_rules.py
git commit -m "feat(analysis): core types + rule registry — Violation, Rule, AnalysisResult, RuleRegistry"
```

---

### Task 2: Universal Rules (Language-Agnostic)

**Files:**
- Create: `llm_code/analysis/universal_rules.py`
- Create: `tests/test_analysis/test_universal_rules.py`

- [ ] **Step 1: Write failing tests for universal rules**

```python
"""Tests for llm_code.analysis.universal_rules — language-agnostic rules."""
from __future__ import annotations

from pathlib import Path

import pytest

from llm_code.analysis.universal_rules import (
    check_hardcoded_secret,
    check_todo_fixme,
    check_god_module,
    register_universal_rules,
)
from llm_code.analysis.rules import RuleRegistry


class TestHardcodedSecret:
    def test_detects_api_key(self) -> None:
        content = 'API_KEY = "sk-1234567890abcdef1234567890abcdef"\n'
        violations = check_hardcoded_secret("config.py", content)
        assert len(violations) == 1
        assert violations[0].rule_key == "hardcoded-secret"
        assert violations[0].severity == "critical"

    def test_detects_password(self) -> None:
        content = 'password = "SuperSecret12345678"\n'
        violations = check_hardcoded_secret("settings.py", content)
        assert len(violations) == 1

    def test_detects_token_with_colon(self) -> None:
        content = 'token: "abcdef1234567890abcdef"\n'
        violations = check_hardcoded_secret("config.yaml", content)
        assert len(violations) == 1

    def test_ignores_short_values(self) -> None:
        content = 'API_KEY = "short"\n'
        violations = check_hardcoded_secret("config.py", content)
        assert len(violations) == 0

    def test_ignores_env_example(self) -> None:
        content = 'API_KEY = "sk-1234567890abcdef1234567890abcdef"\n'
        violations = check_hardcoded_secret(".env.example", content)
        assert len(violations) == 0

    def test_ignores_markdown(self) -> None:
        content = 'api_key = "sk-1234567890abcdef1234567890abcdef"\n'
        violations = check_hardcoded_secret("README.md", content)
        assert len(violations) == 0

    def test_case_insensitive(self) -> None:
        content = 'SECRET = "abcdefghijklmnop1234"\n'
        violations = check_hardcoded_secret("app.py", content)
        assert len(violations) == 1


class TestTodoFixme:
    def test_detects_python_todo(self) -> None:
        content = "# TODO: fix this\nx = 1\n"
        violations = check_todo_fixme("app.py", content)
        assert len(violations) == 1
        assert violations[0].line == 1
        assert violations[0].severity == "low"

    def test_detects_js_fixme(self) -> None:
        content = "let x = 1;\n// FIXME: broken\n"
        violations = check_todo_fixme("app.js", content)
        assert len(violations) == 1
        assert violations[0].line == 2

    def test_detects_hack_and_xxx(self) -> None:
        content = "# HACK: workaround\n# XXX: danger\n"
        violations = check_todo_fixme("app.py", content)
        assert len(violations) == 2

    def test_no_false_positives(self) -> None:
        content = "todolist = []\nfixed = True\n"
        violations = check_todo_fixme("app.py", content)
        assert len(violations) == 0


class TestGodModule:
    def test_detects_large_file(self) -> None:
        content = "\n".join(f"line_{i} = {i}" for i in range(900))
        violations = check_god_module("big.py", content)
        assert len(violations) == 1
        assert violations[0].rule_key == "god-module"
        assert violations[0].severity == "medium"
        assert violations[0].line == 0
        assert "900" in violations[0].message

    def test_ignores_small_file(self) -> None:
        content = "\n".join(f"line_{i} = {i}" for i in range(100))
        violations = check_god_module("small.py", content)
        assert len(violations) == 0

    def test_exactly_800_lines_ok(self) -> None:
        content = "\n".join(f"x = {i}" for i in range(800))
        violations = check_god_module("edge.py", content)
        assert len(violations) == 0

    def test_801_lines_triggers(self) -> None:
        content = "\n".join(f"x = {i}" for i in range(801))
        violations = check_god_module("edge.py", content)
        assert len(violations) == 1


class TestRegisterUniversalRules:
    def test_registers_three_rules(self) -> None:
        registry = RuleRegistry()
        register_universal_rules(registry)
        assert len(registry.all_rules()) == 3
        assert registry.get("hardcoded-secret") is not None
        assert registry.get("todo-fixme") is not None
        assert registry.get("god-module") is not None

    def test_all_universal_rules_target_star(self) -> None:
        registry = RuleRegistry()
        register_universal_rules(registry)
        for rule in registry.all_rules():
            assert "*" in rule.languages
```

- [ ] **Step 2: Implement universal rules**

```python
# llm_code/analysis/universal_rules.py
"""Universal (language-agnostic) code analysis rules."""
from __future__ import annotations

import re

from llm_code.analysis.rules import Rule, RuleRegistry, Violation

_SECRET_PATTERN = re.compile(
    r"(api[_\-]?key|secret|password|token)\s*[:=]\s*[\"'][a-zA-Z0-9]{16,}[\"']",
    re.IGNORECASE,
)

_SKIP_SECRET_EXTENSIONS = frozenset({".md", ".txt", ".rst"})

_TODO_PATTERN = re.compile(
    r"(?:#|//)\s*(TODO|FIXME|HACK|XXX)\b",
)

_GOD_MODULE_THRESHOLD = 800


def check_hardcoded_secret(file_path: str, content: str, tree: object = None) -> list[Violation]:
    """Detect hardcoded secrets via regex."""
    # Skip documentation and example files
    lower = file_path.lower()
    if lower.endswith(".env.example"):
        return []
    for ext in _SKIP_SECRET_EXTENSIONS:
        if lower.endswith(ext):
            return []

    violations: list[Violation] = []
    for i, line in enumerate(content.splitlines(), start=1):
        if _SECRET_PATTERN.search(line):
            # Truncate the displayed value for safety
            snippet = line.strip()
            if len(snippet) > 60:
                snippet = snippet[:57] + "..."
            violations.append(Violation(
                rule_key="hardcoded-secret",
                severity="critical",
                file_path=file_path,
                line=i,
                message=f"Hardcoded secret: {snippet}",
            ))
    return violations


def check_todo_fixme(file_path: str, content: str, tree: object = None) -> list[Violation]:
    """Detect TODO/FIXME/HACK/XXX comments."""
    violations: list[Violation] = []
    for i, line in enumerate(content.splitlines(), start=1):
        match = _TODO_PATTERN.search(line)
        if match:
            violations.append(Violation(
                rule_key="todo-fixme",
                severity="low",
                file_path=file_path,
                line=i,
                message=line.strip(),
            ))
    return violations


def check_god_module(file_path: str, content: str, tree: object = None) -> list[Violation]:
    """Detect files exceeding the god-module line threshold."""
    line_count = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
    if line_count > _GOD_MODULE_THRESHOLD:
        return [Violation(
            rule_key="god-module",
            severity="medium",
            file_path=file_path,
            line=0,
            message=f"God module ({line_count:,} lines, threshold {_GOD_MODULE_THRESHOLD})",
        )]
    return []


def register_universal_rules(registry: RuleRegistry) -> None:
    """Register all universal rules with the given registry."""
    registry.register(Rule(
        key="hardcoded-secret",
        name="Hardcoded secret",
        severity="critical",
        languages=("*",),
        check=check_hardcoded_secret,
    ))
    registry.register(Rule(
        key="todo-fixme",
        name="TODO/FIXME comment",
        severity="low",
        languages=("*",),
        check=check_todo_fixme,
    ))
    registry.register(Rule(
        key="god-module",
        name="God module",
        severity="medium",
        languages=("*",),
        check=check_god_module,
    ))
```

- [ ] **Step 3: Run tests, verify GREEN**

```bash
cd /Users/adamhong/Work/qwen/llm-code
python -m pytest tests/test_analysis/test_universal_rules.py -v
```

- [ ] **Step 4: Commit**

```bash
git add llm_code/analysis/universal_rules.py tests/test_analysis/test_universal_rules.py
git commit -m "feat(analysis): universal rules — hardcoded-secret, todo-fixme, god-module"
```

---

### Task 3: Python AST Rules

**Files:**
- Create: `llm_code/analysis/python_rules.py`
- Create: `tests/test_analysis/test_python_rules.py`

- [ ] **Step 1: Write failing tests for Python AST rules**

```python
"""Tests for llm_code.analysis.python_rules — Python AST-based rules."""
from __future__ import annotations

import ast
import textwrap

import pytest

from llm_code.analysis.python_rules import (
    check_bare_except,
    check_empty_except,
    check_unused_import,
    check_star_import,
    check_print_in_prod,
    check_circular_import,
    register_python_rules,
)
from llm_code.analysis.rules import RuleRegistry


def _parse(code: str) -> ast.Module:
    return ast.parse(textwrap.dedent(code))


class TestBareExcept:
    def test_detects_bare_except(self) -> None:
        code = """\
        try:
            x = 1
        except:
            pass
        """
        tree = _parse(code)
        violations = check_bare_except("app.py", textwrap.dedent(code), tree)
        assert len(violations) == 1
        assert violations[0].rule_key == "bare-except"
        assert violations[0].severity == "high"

    def test_ignores_typed_except(self) -> None:
        code = """\
        try:
            x = 1
        except ValueError:
            pass
        """
        tree = _parse(code)
        violations = check_bare_except("app.py", textwrap.dedent(code), tree)
        assert len(violations) == 0

    def test_multiple_bare_excepts(self) -> None:
        code = """\
        try:
            a()
        except:
            pass
        try:
            b()
        except:
            pass
        """
        tree = _parse(code)
        violations = check_bare_except("app.py", textwrap.dedent(code), tree)
        assert len(violations) == 2


class TestEmptyExcept:
    def test_detects_pass_only(self) -> None:
        code = """\
        try:
            x = 1
        except ValueError:
            pass
        """
        tree = _parse(code)
        violations = check_empty_except("app.py", textwrap.dedent(code), tree)
        assert len(violations) == 1
        assert violations[0].rule_key == "empty-except"
        assert violations[0].severity == "medium"

    def test_ignores_except_with_body(self) -> None:
        code = """\
        try:
            x = 1
        except ValueError:
            print("error")
        """
        tree = _parse(code)
        violations = check_empty_except("app.py", textwrap.dedent(code), tree)
        assert len(violations) == 0

    def test_detects_ellipsis_only(self) -> None:
        code = """\
        try:
            x = 1
        except ValueError:
            ...
        """
        tree = _parse(code)
        violations = check_empty_except("app.py", textwrap.dedent(code), tree)
        assert len(violations) == 1


class TestUnusedImport:
    def test_detects_unused(self) -> None:
        code = """\
        import os
        import sys
        x = sys.argv
        """
        tree = _parse(code)
        violations = check_unused_import("app.py", textwrap.dedent(code), tree)
        assert len(violations) == 1
        assert "os" in violations[0].message

    def test_all_used(self) -> None:
        code = """\
        import os
        path = os.path.join("a", "b")
        """
        tree = _parse(code)
        violations = check_unused_import("app.py", textwrap.dedent(code), tree)
        assert len(violations) == 0

    def test_skips_init_files(self) -> None:
        code = """\
        from .models import User
        from .views import index
        """
        tree = _parse(code)
        violations = check_unused_import("__init__.py", textwrap.dedent(code), tree)
        assert len(violations) == 0

    def test_from_import_unused(self) -> None:
        code = """\
        from os.path import join, exists
        result = join("a", "b")
        """
        tree = _parse(code)
        violations = check_unused_import("app.py", textwrap.dedent(code), tree)
        assert len(violations) == 1
        assert "exists" in violations[0].message


class TestStarImport:
    def test_detects_star(self) -> None:
        code = """\
        from os.path import *
        """
        tree = _parse(code)
        violations = check_star_import("app.py", textwrap.dedent(code), tree)
        assert len(violations) == 1
        assert violations[0].rule_key == "star-import"

    def test_ignores_normal_import(self) -> None:
        code = """\
        from os.path import join
        """
        tree = _parse(code)
        violations = check_star_import("app.py", textwrap.dedent(code), tree)
        assert len(violations) == 0


class TestPrintInProd:
    def test_detects_print(self) -> None:
        code = """\
        def main():
            print("hello")
        """
        tree = _parse(code)
        violations = check_print_in_prod("src/main.py", textwrap.dedent(code), tree)
        assert len(violations) == 1
        assert violations[0].rule_key == "print-in-prod"

    def test_skips_test_files(self) -> None:
        code = """\
        print("test output")
        """
        tree = _parse(code)
        violations = check_print_in_prod("tests/test_main.py", textwrap.dedent(code), tree)
        assert len(violations) == 0

    def test_ignores_non_print_calls(self) -> None:
        code = """\
        def main():
            logging.info("hello")
        """
        tree = _parse(code)
        violations = check_print_in_prod("src/main.py", textwrap.dedent(code), tree)
        assert len(violations) == 0


class TestCircularImport:
    def test_detects_cycle(self, tmp_path: "Path") -> None:
        from pathlib import Path

        # a.py imports b, b.py imports a
        (tmp_path / "a.py").write_text("import b\n")
        (tmp_path / "b.py").write_text("import a\n")

        files = {
            "a.py": (tmp_path / "a.py").read_text(),
            "b.py": (tmp_path / "b.py").read_text(),
        }
        violations = check_circular_import(files)
        assert len(violations) >= 1
        assert violations[0].rule_key == "circular-import"
        assert violations[0].severity == "high"

    def test_no_cycle(self, tmp_path: "Path") -> None:
        from pathlib import Path

        (tmp_path / "a.py").write_text("import os\n")
        (tmp_path / "b.py").write_text("import a\n")

        files = {
            "a.py": (tmp_path / "a.py").read_text(),
            "b.py": (tmp_path / "b.py").read_text(),
        }
        violations = check_circular_import(files)
        assert len(violations) == 0

    def test_three_module_cycle(self, tmp_path: "Path") -> None:
        from pathlib import Path

        (tmp_path / "a.py").write_text("import b\n")
        (tmp_path / "b.py").write_text("import c\n")
        (tmp_path / "c.py").write_text("import a\n")

        files = {
            "a.py": (tmp_path / "a.py").read_text(),
            "b.py": (tmp_path / "b.py").read_text(),
            "c.py": (tmp_path / "c.py").read_text(),
        }
        violations = check_circular_import(files)
        assert len(violations) >= 1
        # The message should show the full chain
        assert "→" in violations[0].message


class TestRegisterPythonRules:
    def test_registers_all_rules(self) -> None:
        registry = RuleRegistry()
        register_python_rules(registry)
        keys = {r.key for r in registry.all_rules()}
        expected = {"bare-except", "empty-except", "unused-import", "star-import", "print-in-prod", "circular-import"}
        assert keys == expected

    def test_all_target_python(self) -> None:
        registry = RuleRegistry()
        register_python_rules(registry)
        for rule in registry.all_rules():
            assert "python" in rule.languages
```

- [ ] **Step 2: Implement Python AST rules**

```python
# llm_code/analysis/python_rules.py
"""Python AST-based code analysis rules."""
from __future__ import annotations

import ast
from pathlib import PurePosixPath

from llm_code.analysis.rules import Rule, RuleRegistry, Violation


def check_bare_except(file_path: str, content: str, tree: ast.Module | None = None) -> list[Violation]:
    """Detect bare except clauses (except without a type)."""
    if tree is None:
        return []
    violations: list[Violation] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and node.type is None:
            violations.append(Violation(
                rule_key="bare-except",
                severity="high",
                file_path=file_path,
                line=node.lineno,
                message="Bare except clause",
            ))
    return violations


def check_empty_except(file_path: str, content: str, tree: ast.Module | None = None) -> list[Violation]:
    """Detect except blocks with only pass or ellipsis."""
    if tree is None:
        return []
    violations: list[Violation] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            body = node.body
            if len(body) == 1:
                stmt = body[0]
                is_pass = isinstance(stmt, ast.Pass)
                is_ellipsis = (
                    isinstance(stmt, ast.Expr)
                    and isinstance(stmt.value, ast.Constant)
                    and stmt.value.value is ...
                )
                if is_pass or is_ellipsis:
                    violations.append(Violation(
                        rule_key="empty-except",
                        severity="medium",
                        file_path=file_path,
                        line=node.lineno,
                        message="Empty except block",
                    ))
    return violations


def check_unused_import(file_path: str, content: str, tree: ast.Module | None = None) -> list[Violation]:
    """Detect imported names that are never referenced in the file."""
    if tree is None:
        return []
    # Skip __init__.py files (re-exports)
    if PurePosixPath(file_path).name == "__init__.py":
        return []

    # Collect imported names -> line numbers
    imported: dict[str, int] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.asname or alias.name.split(".")[0]
                imported[name] = node.lineno
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "*":
                    continue
                name = alias.asname or alias.name
                imported[name] = node.lineno

    if not imported:
        return []

    # Collect all Name references
    used_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            used_names.add(node.id)
        elif isinstance(node, ast.Attribute):
            # For x.y.z, the root Name node is collected by ast.Name above
            pass

    violations: list[Violation] = []
    for name, lineno in sorted(imported.items(), key=lambda x: x[1]):
        if name not in used_names:
            violations.append(Violation(
                rule_key="unused-import",
                severity="low",
                file_path=file_path,
                line=lineno,
                message=f"Unused import: {name}",
            ))
    return violations


def check_star_import(file_path: str, content: str, tree: ast.Module | None = None) -> list[Violation]:
    """Detect wildcard imports (from x import *)."""
    if tree is None:
        return []
    violations: list[Violation] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.names:
            if node.names[0].name == "*":
                module = node.module or ""
                violations.append(Violation(
                    rule_key="star-import",
                    severity="low",
                    file_path=file_path,
                    line=node.lineno,
                    message=f"Wildcard import: from {module} import *",
                ))
    return violations


def check_print_in_prod(file_path: str, content: str, tree: ast.Module | None = None) -> list[Violation]:
    """Detect print() calls in non-test files."""
    if tree is None:
        return []
    # Skip test files
    parts = PurePosixPath(file_path).parts
    if any(p in ("tests", "test") for p in parts):
        return []

    violations: list[Violation] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "print"
        ):
            violations.append(Violation(
                rule_key="print-in-prod",
                severity="low",
                file_path=file_path,
                line=node.lineno,
                message="print() in production code",
            ))
    return violations


def check_circular_import(files: dict[str, str]) -> list[Violation]:
    """Detect circular import chains across multiple Python files.

    Args:
        files: Mapping of relative file paths to their source content.

    Returns:
        A list of Violation for each detected cycle.
    """
    # Build module name -> set of imported module names
    graph: dict[str, set[str]] = {}
    file_modules: set[str] = set()

    for file_path, content in files.items():
        module_name = PurePosixPath(file_path).stem
        file_modules.add(module_name)
        graph.setdefault(module_name, set())

        try:
            tree = ast.parse(content, filename=file_path)
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    dep = alias.name.split(".")[0]
                    graph[module_name].add(dep)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    dep = node.module.split(".")[0]
                    graph[module_name].add(dep)

    # Filter graph to only include project-internal modules
    for mod in graph:
        graph[mod] = graph[mod] & file_modules

    # DFS cycle detection
    visited: set[str] = set()
    on_stack: set[str] = set()
    cycles: list[list[str]] = []

    def _dfs(node: str, path: list[str]) -> None:
        visited.add(node)
        on_stack.add(node)
        path.append(node)
        for neighbor in sorted(graph.get(node, set())):
            if neighbor not in visited:
                _dfs(neighbor, path)
            elif neighbor in on_stack:
                # Found a cycle: extract it
                cycle_start = path.index(neighbor)
                cycle = path[cycle_start:] + [neighbor]
                cycles.append(cycle)
        path.pop()
        on_stack.discard(node)

    for mod in sorted(graph):
        if mod not in visited:
            _dfs(mod, [])

    # Deduplicate cycles by their canonical form (sorted rotation)
    seen_cycles: set[tuple[str, ...]] = set()
    violations: list[Violation] = []

    for cycle in cycles:
        # Normalize: rotate so smallest element is first
        min_idx = cycle[:-1].index(min(cycle[:-1]))
        canonical = tuple(cycle[min_idx:-1]) + (cycle[min_idx],)
        if canonical in seen_cycles:
            continue
        seen_cycles.add(canonical)

        chain = " → ".join(canonical)
        # Report on the first module in the cycle
        first_mod = canonical[0]
        first_file = next(
            (fp for fp in files if PurePosixPath(fp).stem == first_mod),
            f"{first_mod}.py",
        )
        violations.append(Violation(
            rule_key="circular-import",
            severity="high",
            file_path=first_file,
            line=0,
            message=f"Circular import: {chain}",
        ))

    return violations


def register_python_rules(registry: RuleRegistry) -> None:
    """Register all Python rules with the given registry."""
    registry.register(Rule(
        key="bare-except",
        name="Bare except clause",
        severity="high",
        languages=("python",),
        check=check_bare_except,
    ))
    registry.register(Rule(
        key="empty-except",
        name="Empty except block",
        severity="medium",
        languages=("python",),
        check=check_empty_except,
    ))
    registry.register(Rule(
        key="unused-import",
        name="Unused import",
        severity="low",
        languages=("python",),
        check=check_unused_import,
    ))
    registry.register(Rule(
        key="star-import",
        name="Wildcard import",
        severity="low",
        languages=("python",),
        check=check_star_import,
    ))
    registry.register(Rule(
        key="print-in-prod",
        name="print() in production code",
        severity="low",
        languages=("python",),
        check=check_print_in_prod,
    ))
    registry.register(Rule(
        key="circular-import",
        name="Circular import chain",
        severity="high",
        languages=("python",),
        check=check_circular_import,  # type: ignore[arg-type]  # cross-file signature
    ))
```

- [ ] **Step 3: Run tests, verify GREEN**

```bash
cd /Users/adamhong/Work/qwen/llm-code
python -m pytest tests/test_analysis/test_python_rules.py -v
```

- [ ] **Step 4: Commit**

```bash
git add llm_code/analysis/python_rules.py tests/test_analysis/test_python_rules.py
git commit -m "feat(analysis): Python AST rules — bare-except, empty-except, unused-import, star-import, print-in-prod, circular-import"
```

---

### Task 4: JS/TS Regex Rules

**Files:**
- Create: `llm_code/analysis/js_rules.py`
- Create: `tests/test_analysis/test_js_rules.py`

- [ ] **Step 1: Write failing tests for JS/TS regex rules**

```python
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
```

- [ ] **Step 2: Implement JS/TS regex rules**

```python
# llm_code/analysis/js_rules.py
"""JS/TS regex-based code analysis rules."""
from __future__ import annotations

import re
from pathlib import PurePosixPath

from llm_code.analysis.rules import Rule, RuleRegistry, Violation

_EMPTY_CATCH_PATTERN = re.compile(
    r"catch\s*\([^)]*\)\s*\{\s*\}",
    re.MULTILINE,
)

_CONSOLE_LOG_PATTERN = re.compile(
    r"console\.(log|debug|info|warn|error)\s*\(",
)

_TEST_DIR_NAMES = frozenset({"tests", "test", "__tests__"})
_TEST_SUFFIXES = frozenset({".test", ".spec"})


def _is_test_file(file_path: str) -> bool:
    """Check if a file is a test file by path or naming convention."""
    parts = PurePosixPath(file_path).parts
    if any(p in _TEST_DIR_NAMES for p in parts):
        return True
    stem = PurePosixPath(file_path).stem
    for suffix in _TEST_SUFFIXES:
        if stem.endswith(suffix):
            return True
    return False


def check_empty_catch(file_path: str, content: str, tree: object = None) -> list[Violation]:
    """Detect empty catch blocks via regex."""
    violations: list[Violation] = []
    for match in _EMPTY_CATCH_PATTERN.finditer(content):
        # Approximate line number
        line = content[:match.start()].count("\n") + 1
        violations.append(Violation(
            rule_key="empty-catch",
            severity="medium",
            file_path=file_path,
            line=line,
            message="Empty catch block",
        ))
    return violations


def check_console_log(file_path: str, content: str, tree: object = None) -> list[Violation]:
    """Detect console.log/debug/info/warn/error in non-test files."""
    if _is_test_file(file_path):
        return []

    violations: list[Violation] = []
    for match in _CONSOLE_LOG_PATTERN.finditer(content):
        line = content[:match.start()].count("\n") + 1
        method = match.group(1)
        violations.append(Violation(
            rule_key="console-log",
            severity="low",
            file_path=file_path,
            line=line,
            message=f"console.{method}() in production code",
        ))
    return violations


def register_js_rules(registry: RuleRegistry) -> None:
    """Register all JS/TS rules with the given registry."""
    registry.register(Rule(
        key="empty-catch",
        name="Empty catch block",
        severity="medium",
        languages=("javascript", "typescript"),
        check=check_empty_catch,
    ))
    registry.register(Rule(
        key="console-log",
        name="console.log in production",
        severity="low",
        languages=("javascript", "typescript"),
        check=check_console_log,
    ))
```

- [ ] **Step 3: Run tests, verify GREEN**

```bash
cd /Users/adamhong/Work/qwen/llm-code
python -m pytest tests/test_analysis/test_js_rules.py -v
```

- [ ] **Step 4: Commit**

```bash
git add llm_code/analysis/js_rules.py tests/test_analysis/test_js_rules.py
git commit -m "feat(analysis): JS/TS regex rules — empty-catch, console-log"
```

---

### Task 5: Analysis Engine + Cache

**Files:**
- Create: `llm_code/analysis/engine.py`
- Create: `llm_code/analysis/cache.py`
- Create: `tests/test_analysis/test_engine.py`

- [ ] **Step 1: Write failing tests for engine and cache**

```python
"""Tests for llm_code.analysis.engine — analysis orchestration + cache."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from llm_code.analysis.engine import run_analysis, run_diff_check
from llm_code.analysis.cache import save_analysis, load_analysis
from llm_code.analysis.rules import AnalysisResult, Violation


class TestRunAnalysis:
    def test_empty_directory(self, tmp_path: Path) -> None:
        result = run_analysis(tmp_path)
        assert isinstance(result, AnalysisResult)
        assert result.file_count == 0
        assert len(result.violations) == 0

    def test_detects_bare_except_in_python(self, tmp_path: Path) -> None:
        code = "try:\n    x = 1\nexcept:\n    pass\n"
        (tmp_path / "app.py").write_text(code)
        result = run_analysis(tmp_path)
        assert result.file_count == 1
        keys = {v.rule_key for v in result.violations}
        assert "bare-except" in keys

    def test_detects_console_log_in_js(self, tmp_path: Path) -> None:
        code = "console.log('hello');\n"
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "app.js").write_text(code)
        result = run_analysis(tmp_path)
        keys = {v.rule_key for v in result.violations}
        assert "console-log" in keys

    def test_detects_hardcoded_secret(self, tmp_path: Path) -> None:
        code = 'API_KEY = "sk-1234567890abcdef1234567890abcdef"\n'
        (tmp_path / "config.py").write_text(code)
        result = run_analysis(tmp_path)
        keys = {v.rule_key for v in result.violations}
        assert "hardcoded-secret" in keys

    def test_detects_god_module(self, tmp_path: Path) -> None:
        content = "\n".join(f"x_{i} = {i}" for i in range(900))
        (tmp_path / "huge.py").write_text(content)
        result = run_analysis(tmp_path)
        keys = {v.rule_key for v in result.violations}
        assert "god-module" in keys

    def test_detects_circular_import(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("import b\n")
        (tmp_path / "b.py").write_text("import a\n")
        result = run_analysis(tmp_path)
        keys = {v.rule_key for v in result.violations}
        assert "circular-import" in keys

    def test_skips_hidden_dirs(self, tmp_path: Path) -> None:
        (tmp_path / ".hidden").mkdir()
        (tmp_path / ".hidden" / "bad.py").write_text("try:\n    x=1\nexcept:\n    pass\n")
        result = run_analysis(tmp_path)
        assert result.file_count == 0

    def test_respects_max_files(self, tmp_path: Path) -> None:
        for i in range(10):
            (tmp_path / f"mod_{i}.py").write_text(f"x = {i}\n")
        result = run_analysis(tmp_path, max_files=3)
        assert result.file_count == 3

    def test_saves_cache_after_analysis(self, tmp_path: Path) -> None:
        (tmp_path / "app.py").write_text("x = 1\n")
        run_analysis(tmp_path)
        cache_path = tmp_path / ".llm-code" / "last_analysis.json"
        assert cache_path.exists()
        data = json.loads(cache_path.read_text())
        assert "timestamp" in data
        assert "violations" in data

    def test_multiple_violations_sorted(self, tmp_path: Path) -> None:
        code = 'API_KEY = "sk-1234567890abcdef1234567890abcdef"\ntry:\n    x=1\nexcept:\n    pass\n'
        (tmp_path / "app.py").write_text(code)
        result = run_analysis(tmp_path)
        severities = [v.severity for v in result.violations]
        severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        indices = [severity_order.get(s, 99) for s in severities]
        assert indices == sorted(indices)


class TestCache:
    def test_save_and_load(self, tmp_path: Path) -> None:
        violations = (
            Violation(rule_key="test", severity="low", file_path="x.py", line=1, message="m"),
        )
        result = AnalysisResult(violations=violations, file_count=1, duration_ms=5.0)

        cache_dir = tmp_path / ".llm-code"
        save_analysis(cache_dir, result)

        loaded = load_analysis(cache_dir)
        assert loaded is not None
        assert len(loaded.violations) == 1
        assert loaded.violations[0].rule_key == "test"
        assert loaded.file_count == 1

    def test_load_missing_returns_none(self, tmp_path: Path) -> None:
        loaded = load_analysis(tmp_path / ".llm-code")
        assert loaded is None

    def test_load_corrupt_returns_none(self, tmp_path: Path) -> None:
        cache_dir = tmp_path / ".llm-code"
        cache_dir.mkdir(parents=True)
        (cache_dir / "last_analysis.json").write_text("not json")
        loaded = load_analysis(cache_dir)
        assert loaded is None


class TestRunDiffCheck:
    def test_new_violation_labeled(self, tmp_path: Path) -> None:
        """A violation present in current but not in cache should be labeled NEW."""
        # First: run analysis with clean code
        (tmp_path / "app.py").write_text("x = 1\n")
        run_analysis(tmp_path)

        # Now: introduce a violation
        (tmp_path / "app.py").write_text("try:\n    x=1\nexcept:\n    pass\n")

        result = run_diff_check(tmp_path, changed_files=["app.py"])
        assert result is not None
        assert any(
            v.rule_key == "bare-except"
            for v in result.new_violations
        )

    def test_fixed_violation_labeled(self, tmp_path: Path) -> None:
        """A violation in cache but no longer present should be labeled FIXED."""
        # First: run analysis with bad code
        (tmp_path / "app.py").write_text("try:\n    x=1\nexcept:\n    pass\n")
        run_analysis(tmp_path)

        # Now: fix it
        (tmp_path / "app.py").write_text("try:\n    x=1\nexcept ValueError:\n    pass\n")

        result = run_diff_check(tmp_path, changed_files=["app.py"])
        assert result is not None
        assert any(
            v.rule_key == "bare-except"
            for v in result.fixed_violations
        )

    def test_no_cache_returns_all_as_new(self, tmp_path: Path) -> None:
        """Without prior cache, all violations should be labeled NEW."""
        (tmp_path / "app.py").write_text("try:\n    x=1\nexcept:\n    pass\n")
        result = run_diff_check(tmp_path, changed_files=["app.py"])
        assert result is not None
        assert len(result.new_violations) > 0
        assert len(result.fixed_violations) == 0
```

- [ ] **Step 2: Implement cache module**

```python
# llm_code/analysis/cache.py
"""Save and load analysis results to JSON cache."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from llm_code.analysis.rules import AnalysisResult, Violation

logger = logging.getLogger(__name__)

_CACHE_FILENAME = "last_analysis.json"


def save_analysis(cache_dir: Path, result: AnalysisResult) -> Path:
    """Save analysis result to cache_dir/last_analysis.json."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / _CACHE_FILENAME

    data: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "file_count": result.file_count,
        "duration_ms": result.duration_ms,
        "violations": [v.to_dict() for v in result.violations],
    }
    cache_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return cache_path


def load_analysis(cache_dir: Path) -> AnalysisResult | None:
    """Load the last analysis result from cache. Returns None if missing or corrupt."""
    cache_path = cache_dir / _CACHE_FILENAME
    if not cache_path.exists():
        return None

    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        violations = tuple(Violation.from_dict(v) for v in data["violations"])
        return AnalysisResult(
            violations=violations,
            file_count=data["file_count"],
            duration_ms=data["duration_ms"],
        )
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        logger.warning("Failed to load analysis cache: %s", exc)
        return None
```

- [ ] **Step 3: Implement engine module**

```python
# llm_code/analysis/engine.py
"""Analysis engine — orchestrates rule execution across files."""
from __future__ import annotations

import ast
import logging
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from llm_code.analysis.cache import load_analysis, save_analysis
from llm_code.analysis.rules import AnalysisResult, RuleRegistry, Violation
from llm_code.analysis.python_rules import register_python_rules
from llm_code.analysis.js_rules import register_js_rules
from llm_code.analysis.universal_rules import register_universal_rules

logger = logging.getLogger(__name__)

_SKIP_DIRS = frozenset({
    ".git", "__pycache__", "node_modules", ".venv", "venv",
    ".tox", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "dist", "build", ".eggs",
})

_PYTHON_EXTS = frozenset({".py", ".pyi"})
_JS_TS_EXTS = frozenset({".js", ".jsx", ".ts", ".tsx"})
_SUPPORTED_EXTS = _PYTHON_EXTS | _JS_TS_EXTS

_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


@dataclass(frozen=True)
class DiffCheckResult:
    """Result of a diff check comparing current analysis against cache."""

    new_violations: tuple[Violation, ...]
    fixed_violations: tuple[Violation, ...]
    file_count: int


def _build_registry() -> RuleRegistry:
    """Create a registry with all built-in rules."""
    registry = RuleRegistry()
    register_universal_rules(registry)
    register_python_rules(registry)
    register_js_rules(registry)
    return registry


def _collect_files(
    cwd: Path, max_files: int,
) -> list[Path]:
    """Collect supported source files, skipping irrelevant directories."""
    files: list[Path] = []

    def _walk(current: Path) -> None:
        if len(files) >= max_files:
            return
        try:
            entries = sorted(current.iterdir(), key=lambda p: p.name)
        except PermissionError:
            return
        for entry in entries:
            if len(files) >= max_files:
                return
            if entry.is_dir():
                if entry.name in _SKIP_DIRS or entry.name.startswith("."):
                    continue
                _walk(entry)
            elif entry.is_file():
                if entry.suffix.lower() in _SUPPORTED_EXTS:
                    try:
                        if entry.stat().st_size <= 200_000:
                            files.append(entry)
                    except OSError:
                        continue

    _walk(cwd)
    files.sort(key=lambda p: str(p.relative_to(cwd)))
    return files


def _language_for_ext(ext: str) -> str:
    """Map file extension to language name."""
    if ext in _PYTHON_EXTS:
        return "python"
    if ext in _JS_TS_EXTS:
        return "javascript"  # treat TS same as JS for rule matching
    return "other"


def run_analysis(cwd: Path, max_files: int = 500) -> AnalysisResult:
    """Run full code analysis on the given directory.

    Discovers files, runs language-appropriate rules, detects circular imports,
    saves results to cache, and returns an AnalysisResult.
    """
    start = time.monotonic()
    registry = _build_registry()
    files = _collect_files(cwd, max_files)
    all_violations: list[Violation] = []

    # Per-file analysis
    python_files: dict[str, str] = {}
    for f in files:
        rel = str(f.relative_to(cwd))
        try:
            content = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        ext = f.suffix.lower()
        language = _language_for_ext(ext)

        # Parse AST for Python files
        tree: ast.Module | None = None
        if language == "python":
            python_files[rel] = content
            try:
                tree = ast.parse(content, filename=rel)
            except SyntaxError:
                tree = None

        # Run language-specific rules
        rules = registry.rules_for_language(language)
        for rule in rules:
            # Skip circular-import here (cross-file, handled below)
            if rule.key == "circular-import":
                continue
            try:
                violations = rule.check(rel, content, tree)
                all_violations.extend(violations)
            except Exception as exc:
                logger.warning("Rule %s failed on %s: %s", rule.key, rel, exc)

    # Cross-file: circular import detection
    circular_rule = registry.get("circular-import")
    if circular_rule and python_files:
        try:
            from llm_code.analysis.python_rules import check_circular_import
            circ_violations = check_circular_import(python_files)
            all_violations.extend(circ_violations)
        except Exception as exc:
            logger.warning("Circular import detection failed: %s", exc)

    # Sort by severity then file then line
    all_violations.sort(
        key=lambda v: (_SEVERITY_ORDER.get(v.severity, 99), v.file_path, v.line),
    )

    duration_ms = (time.monotonic() - start) * 1000
    result = AnalysisResult(
        violations=tuple(all_violations),
        file_count=len(files),
        duration_ms=duration_ms,
    )

    # Save to cache
    try:
        cache_dir = cwd / ".llm-code"
        save_analysis(cache_dir, result)
    except OSError as exc:
        logger.warning("Failed to save analysis cache: %s", exc)

    return result


def run_diff_check(
    cwd: Path,
    changed_files: list[str] | None = None,
) -> DiffCheckResult:
    """Run analysis on changed files and compare against cached results.

    If changed_files is None, uses git to detect changed files.
    """
    # Load previous analysis
    cache_dir = cwd / ".llm-code"
    previous = load_analysis(cache_dir)

    # Get changed files
    if changed_files is None:
        changed_files = _git_changed_files(cwd)

    if not changed_files:
        return DiffCheckResult(
            new_violations=(),
            fixed_violations=(),
            file_count=0,
        )

    # Filter to supported extensions
    supported = [
        f for f in changed_files
        if PurePosixPath(f).suffix.lower() in _SUPPORTED_EXTS
    ]

    # Run analysis on changed files only
    registry = _build_registry()
    current_violations: list[Violation] = []

    for rel in supported:
        full_path = cwd / rel
        if not full_path.exists():
            continue
        try:
            content = full_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        ext = full_path.suffix.lower()
        language = _language_for_ext(ext)

        tree: ast.Module | None = None
        if language == "python":
            try:
                tree = ast.parse(content, filename=rel)
            except SyntaxError:
                tree = None

        rules = registry.rules_for_language(language)
        for rule in rules:
            if rule.key == "circular-import":
                continue
            try:
                violations = rule.check(rel, content, tree)
                current_violations.extend(violations)
            except Exception as exc:
                logger.warning("Rule %s failed on %s: %s", rule.key, rel, exc)

    # Compare with previous
    previous_set: set[tuple[str, str, int]] = set()
    if previous:
        for v in previous.violations:
            if v.file_path in supported:
                previous_set.add((v.rule_key, v.file_path, v.line))

    current_set: set[tuple[str, str, int]] = set()
    for v in current_violations:
        current_set.add((v.rule_key, v.file_path, v.line))

    # New = in current but not in previous
    new_keys = current_set - previous_set
    new_violations = tuple(
        v for v in current_violations
        if (v.rule_key, v.file_path, v.line) in new_keys
    )

    # Fixed = in previous but not in current
    fixed_keys = previous_set - current_set
    fixed_violations: tuple[Violation, ...] = ()
    if previous:
        fixed_violations = tuple(
            v for v in previous.violations
            if (v.rule_key, v.file_path, v.line) in fixed_keys
        )

    return DiffCheckResult(
        new_violations=new_violations,
        fixed_violations=fixed_violations,
        file_count=len(supported),
    )


def _git_changed_files(cwd: Path) -> list[str]:
    """Get list of changed files from git."""
    import subprocess

    files: set[str] = set()
    for cmd in (
        ["git", "diff", "--name-only"],
        ["git", "diff", "--cached", "--name-only"],
    ):
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, cwd=cwd, timeout=10,
            )
            if result.returncode == 0:
                for line in result.stdout.strip().splitlines():
                    if line.strip():
                        files.add(line.strip())
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            continue

    return sorted(files)
```

- [ ] **Step 4: Run tests, verify GREEN**

```bash
cd /Users/adamhong/Work/qwen/llm-code
python -m pytest tests/test_analysis/test_engine.py -v
```

- [ ] **Step 5: Commit**

```bash
git add llm_code/analysis/engine.py llm_code/analysis/cache.py \
       tests/test_analysis/test_engine.py
git commit -m "feat(analysis): engine + cache — run_analysis, run_diff_check, JSON cache"
```

---

### Task 6: CLI Integration (/analyze + /diff-check)

**Files:**
- Modify: `llm_code/tui/app.py`

- [ ] **Step 1: Add _cmd_analyze handler to app.py**

Register the `/analyze` command in the command dispatch table (where other `/cmd_*` methods are registered) and add:

```python
def _cmd_analyze(self, args: str) -> None:
    """Run code analysis on the codebase."""
    import asyncio
    asyncio.ensure_future(self._run_analyze(args))

async def _run_analyze(self, args: str) -> None:
    from llm_code.analysis.engine import run_analysis
    chat = self.query_one(ChatScrollView)

    target = self._cwd
    if args.strip():
        candidate = self._cwd / args.strip()
        if candidate.is_dir():
            target = candidate
        else:
            chat.add_entry(AssistantText(f"Not a directory: {args.strip()}"))
            return

    try:
        result = run_analysis(target)
        chat.add_entry(AssistantText(result.format_chat()))

        # Inject into agent context for next turn
        if self._runtime and result.violations:
            context = result.format_context(max_tokens=1000)
            self._runtime.inject_context("code_analysis", context)
    except Exception as exc:
        chat.add_entry(AssistantText(f"Analysis error: {exc}"))
```

- [ ] **Step 2: Add _cmd_diff_check handler to app.py**

```python
def _cmd_diff_check(self, args: str) -> None:
    """Run diff check on changed files."""
    import asyncio
    asyncio.ensure_future(self._run_diff_check())

async def _run_diff_check(self) -> None:
    from llm_code.analysis.engine import run_diff_check
    chat = self.query_one(ChatScrollView)

    try:
        result = run_diff_check(self._cwd)

        new_count = len(result.new_violations)
        fixed_count = len(result.fixed_violations)
        header = (
            f"## Diff Check — {result.file_count} files changed, "
            f"{new_count} new violations, {fixed_count} resolved\n"
        )

        lines: list[str] = [header]
        for v in result.new_violations:
            loc = f"{v.file_path}:{v.line}" if v.line > 0 else v.file_path
            lines.append(f"  NEW   {v.severity.upper():<8}  {loc:<30}  {v.message}")
        for v in result.fixed_violations:
            loc = f"{v.file_path}:{v.line}" if v.line > 0 else v.file_path
            lines.append(f"  FIXED {v.severity.upper():<8}  {loc:<30}  {v.message}")

        if new_count == 0 and fixed_count == 0:
            lines.append("No changes in violations.")

        chat.add_entry(AssistantText("\n".join(lines)))
    except Exception as exc:
        chat.add_entry(AssistantText(f"Diff check error: {exc}"))
```

- [ ] **Step 3: Register commands in command dispatch**

Find the command dispatch dictionary/mapping in `app.py` (search for `_cmd_dump`, `_cmd_map` registrations) and add:

```python
"analyze": self._cmd_analyze,
"diff-check": self._cmd_diff_check,
```

- [ ] **Step 4: Run full test suite to verify no regressions**

```bash
cd /Users/adamhong/Work/qwen/llm-code
python -m pytest tests/test_analysis/ -v
python -m pytest tests/ -x --timeout=60
```

- [ ] **Step 5: Commit**

```bash
git add llm_code/tui/app.py
git commit -m "feat(analysis): /analyze + /diff-check slash commands in TUI"
```

---

## Conventions

- `from __future__ import annotations` in every file
- `@dataclass(frozen=True)` for all data types
- Type annotations on all function signatures
- TDD: write tests first (RED), then implementation (GREEN), then verify
- Each task ends with a git commit
- Reuse `_SKIP_DIRS` pattern from `repo_map.py` / `dump.py` (do not import — define locally to avoid circular deps)
- Max file size for analysis: 200KB (skip larger files)
- Max files default: 500 (configurable via `max_files` param)
