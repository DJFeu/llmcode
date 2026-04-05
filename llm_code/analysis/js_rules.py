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
