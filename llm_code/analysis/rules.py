"""Core types and rule registry for code analysis."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
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
