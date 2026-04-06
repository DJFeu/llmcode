"""Rust regex-based code analysis rules."""
from __future__ import annotations

import re
from pathlib import PurePosixPath

from llm_code.analysis.rules import Rule, RuleRegistry, Violation

_UNWRAP_PATTERN = re.compile(r"\.(unwrap|expect)\s*\(")
_TODO_MACRO_PATTERN = re.compile(r"\b(todo|unimplemented)!\s*\(")
_UNSAFE_BLOCK_PATTERN = re.compile(r"\bunsafe\s*\{")

_TEST_DIR_NAMES = frozenset({"tests", "test"})
_TEST_SUFFIXES = ("_test.rs",)


def _is_test_file(file_path: str) -> bool:
    parts = PurePosixPath(file_path).parts
    if any(p in _TEST_DIR_NAMES for p in parts):
        return True
    return any(file_path.endswith(s) for s in _TEST_SUFFIXES)


def check_unwrap(file_path: str, content: str, tree: object = None) -> list[Violation]:
    """Detect .unwrap() and .expect() in non-test Rust files."""
    if _is_test_file(file_path):
        return []

    violations: list[Violation] = []
    for match in _UNWRAP_PATTERN.finditer(content):
        line = content[: match.start()].count("\n") + 1
        method = match.group(1)
        violations.append(
            Violation(
                rule_key="rust-unwrap",
                severity="medium",
                file_path=file_path,
                line=line,
                message=f".{method}() in production code — prefer ? or explicit error handling",
            )
        )
    return violations


def check_todo_macro(file_path: str, content: str, tree: object = None) -> list[Violation]:
    """Detect todo!() and unimplemented!() macros."""
    violations: list[Violation] = []
    for match in _TODO_MACRO_PATTERN.finditer(content):
        line = content[: match.start()].count("\n") + 1
        macro = match.group(1)
        violations.append(
            Violation(
                rule_key="rust-todo-macro",
                severity="medium",
                file_path=file_path,
                line=line,
                message=f"{macro}!() macro — incomplete implementation",
            )
        )
    return violations


def check_unsafe_block(file_path: str, content: str, tree: object = None) -> list[Violation]:
    """Detect unsafe blocks for review."""
    violations: list[Violation] = []
    for match in _UNSAFE_BLOCK_PATTERN.finditer(content):
        line = content[: match.start()].count("\n") + 1
        violations.append(
            Violation(
                rule_key="rust-unsafe-block",
                severity="high",
                file_path=file_path,
                line=line,
                message="unsafe block — requires safety review",
            )
        )
    return violations


def register_rust_rules(registry: RuleRegistry) -> None:
    """Register all Rust rules with the given registry."""
    registry.register(
        Rule(
            key="rust-unwrap",
            name=".unwrap() in production",
            severity="medium",
            languages=("rust",),
            check=check_unwrap,
        )
    )
    registry.register(
        Rule(
            key="rust-todo-macro",
            name="todo!/unimplemented! macro",
            severity="medium",
            languages=("rust",),
            check=check_todo_macro,
        )
    )
    registry.register(
        Rule(
            key="rust-unsafe-block",
            name="unsafe block",
            severity="high",
            languages=("rust",),
            check=check_unsafe_block,
        )
    )
