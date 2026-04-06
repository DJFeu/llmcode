"""Go regex-based code analysis rules."""
from __future__ import annotations

import re

from llm_code.analysis.rules import Rule, RuleRegistry, Violation

_EMPTY_ERROR_CHECK = re.compile(
    r"if\s+err\s*!=\s*nil\s*\{\s*\}",
    re.MULTILINE,
)

_FMT_PRINT_PATTERN = re.compile(
    r"fmt\.(Println|Printf|Print)\s*\(",
)

_UNDERSCORE_ERROR = re.compile(
    r"_\s*=\s*\w+[\w.]*\([^)]*\)",
)

_TEST_SUFFIXES = ("_test.go",)


def _is_test_file(file_path: str) -> bool:
    return any(file_path.endswith(s) for s in _TEST_SUFFIXES)


def check_empty_error_check(file_path: str, content: str, tree: object = None) -> list[Violation]:
    """Detect empty error check blocks: if err != nil { }."""
    violations: list[Violation] = []
    for match in _EMPTY_ERROR_CHECK.finditer(content):
        line = content[: match.start()].count("\n") + 1
        violations.append(
            Violation(
                rule_key="go-empty-error-check",
                severity="high",
                file_path=file_path,
                line=line,
                message="Empty error check: if err != nil {}",
            )
        )
    return violations


def check_fmt_println(file_path: str, content: str, tree: object = None) -> list[Violation]:
    """Detect fmt.Println/Printf/Print in non-test Go files."""
    if _is_test_file(file_path):
        return []

    violations: list[Violation] = []
    for match in _FMT_PRINT_PATTERN.finditer(content):
        line = content[: match.start()].count("\n") + 1
        method = match.group(1)
        violations.append(
            Violation(
                rule_key="go-fmt-println",
                severity="low",
                file_path=file_path,
                line=line,
                message=f"fmt.{method}() in production code — prefer structured logging",
            )
        )
    return violations


def check_underscore_error(file_path: str, content: str, tree: object = None) -> list[Violation]:
    """Detect discarded errors: _ = someFunc()."""
    if _is_test_file(file_path):
        return []

    violations: list[Violation] = []
    for match in _UNDERSCORE_ERROR.finditer(content):
        line = content[: match.start()].count("\n") + 1
        violations.append(
            Violation(
                rule_key="go-underscore-error",
                severity="medium",
                file_path=file_path,
                line=line,
                message=f"Discarded error: {match.group(0).strip()}",
            )
        )
    return violations


def register_go_rules(registry: RuleRegistry) -> None:
    """Register all Go rules with the given registry."""
    registry.register(
        Rule(
            key="go-empty-error-check",
            name="Empty error check",
            severity="high",
            languages=("go",),
            check=check_empty_error_check,
        )
    )
    registry.register(
        Rule(
            key="go-fmt-println",
            name="fmt.Println in production",
            severity="low",
            languages=("go",),
            check=check_fmt_println,
        )
    )
    registry.register(
        Rule(
            key="go-underscore-error",
            name="Discarded error",
            severity="medium",
            languages=("go",),
            check=check_underscore_error,
        )
    )
