"""Universal code analysis rules — language-agnostic checks."""
from __future__ import annotations

import re

from llm_code.analysis.rules import Rule, RuleRegistry, Violation

# Files to skip for hardcoded-secret detection
_SECRET_SKIP_SUFFIXES = (".md", ".txt")
_SECRET_SKIP_NAMES = {".env.example"}

# Regex patterns
_SECRET_PATTERN = re.compile(
    r"(api[_\-]?key|secret|password|token)\s*[:=]\s*[\"'][a-zA-Z0-9]{16,}[\"']",
    re.IGNORECASE,
)
_TODO_PATTERN = re.compile(r"(#|//)\s*(TODO|FIXME|HACK|XXX)\b")


def check_hardcoded_secret(file_path: str, content: str) -> list[Violation]:
    """Detect hardcoded secrets (API keys, passwords, tokens) in source code."""
    import os

    basename = os.path.basename(file_path)

    # Skip excluded file types
    if basename in _SECRET_SKIP_NAMES:
        return []
    for suffix in _SECRET_SKIP_SUFFIXES:
        if file_path.endswith(suffix):
            return []

    violations: list[Violation] = []
    for line_no, line in enumerate(content.splitlines(), start=1):
        if _SECRET_PATTERN.search(line):
            violations.append(
                Violation(
                    rule_key="hardcoded-secret",
                    severity="critical",
                    file_path=file_path,
                    line=line_no,
                    message=f"Hardcoded secret detected on line {line_no}",
                )
            )
    return violations


def check_todo_fixme(file_path: str, content: str) -> list[Violation]:
    """Detect TODO, FIXME, HACK, and XXX comment markers."""
    violations: list[Violation] = []
    for line_no, line in enumerate(content.splitlines(), start=1):
        m = _TODO_PATTERN.search(line)
        if m:
            matched_keyword = m.group(2)
            violations.append(
                Violation(
                    rule_key="todo-fixme",
                    severity="low",
                    file_path=file_path,
                    line=line_no,
                    message=f"{matched_keyword} comment found",
                )
            )
    return violations


def check_god_module(file_path: str, content: str) -> list[Violation]:
    """Detect files exceeding 800 lines (god modules)."""
    line_count = len(content.splitlines())
    if line_count > 800:
        return [
            Violation(
                rule_key="god-module",
                severity="medium",
                file_path=file_path,
                line=0,
                message=f"File has {line_count} lines (limit: 800)",
            )
        ]
    return []


def register_universal_rules(registry: RuleRegistry) -> None:
    """Register all universal rules into the given registry."""
    registry.register(
        Rule(
            key="hardcoded-secret",
            name="Hardcoded Secret",
            severity="critical",
            languages=("*",),
            check=check_hardcoded_secret,
        )
    )
    registry.register(
        Rule(
            key="todo-fixme",
            name="TODO / FIXME Comment",
            severity="low",
            languages=("*",),
            check=check_todo_fixme,
        )
    )
    registry.register(
        Rule(
            key="god-module",
            name="God Module",
            severity="medium",
            languages=("*",),
            check=check_god_module,
        )
    )
