"""Execution policy engine — layered command safety rules.

Borrowed from Codex CLI's ``exec_policy.rs`` + ``.rules`` files.

Replaces hardcoded command checks with an extensible rule system:
    built-in rules → project rules → session amendments

Rule format (``.rules`` files)::

    pattern = git commit*
    after = git add*
    decision = allow

Design:
    - Rules are evaluated **first-match-wins** (order matters)
    - ``deny`` rules are **immutable** — session amendments cannot override
    - ``after`` is optional conditional: rule only matches if ``after``
      pattern was seen in recent command history
    - Unknown commands fall through to ``"prompt"`` (safe default)

Risk mitigations:
    - ``deny`` is immutable — cannot be amended at runtime
    - First-match prevents ambiguity
    - Fallthrough = prompt (fail-safe)
    - Rules file parser is strict: malformed blocks are skipped with warning
"""
from __future__ import annotations

import fnmatch
import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PolicyRule:
    """A single execution policy rule."""
    pattern: str
    decision: str       # "allow" | "deny" | "prompt"
    after: str | None = None
    reason: str = ""
    immutable: bool = False  # True for deny rules — cannot be overridden


@dataclass
class ExecPolicy:
    """Layered execution policy with session amendments.

    Evaluation order:
        1. Session amendments (user-granted runtime rules)
        2. Project rules (.llm-code/exec.rules)
        3. Built-in rules (default.rules)
        4. Fallthrough → "prompt"
    """
    _builtin_rules: list[PolicyRule] = field(default_factory=list)
    _project_rules: list[PolicyRule] = field(default_factory=list)
    _session_amendments: list[PolicyRule] = field(default_factory=list)
    _command_history: list[str] = field(default_factory=list)

    def evaluate(self, command: str) -> tuple[str, str]:
        """Evaluate a command against all rule layers.

        Returns (decision, reason) where decision is "allow", "deny", or "prompt".
        """
        # Check session amendments first (user-granted overrides)
        for rule in self._session_amendments:
            if self._matches(rule, command):
                return rule.decision, rule.reason

        # Then project rules
        for rule in self._project_rules:
            if self._matches(rule, command):
                return rule.decision, rule.reason

        # Then built-in rules
        for rule in self._builtin_rules:
            if self._matches(rule, command):
                return rule.decision, rule.reason

        # Fallthrough: prompt (fail-safe)
        return "prompt", ""

    def record_command(self, command: str) -> None:
        """Record a command in history for ``after`` conditional matching."""
        self._command_history.append(command)
        # Keep last 50 commands
        if len(self._command_history) > 50:
            self._command_history = self._command_history[-50:]

    def amend(self, rule: PolicyRule) -> bool:
        """Add a session-scoped rule.

        Returns False if the amendment would conflict with an immutable
        deny rule (the amendment is rejected).
        """
        # Check for immutable deny conflict
        for existing in self._builtin_rules + self._project_rules:
            if (
                existing.immutable
                and existing.decision == "deny"
                and fnmatch.fnmatch(rule.pattern, existing.pattern)
            ):
                logger.warning(
                    "Amendment rejected: pattern '%s' conflicts with immutable "
                    "deny rule '%s' (%s)",
                    rule.pattern, existing.pattern, existing.reason,
                )
                return False

        self._session_amendments.append(rule)
        return True

    def _matches(self, rule: PolicyRule, command: str) -> bool:
        """Check if a rule matches a command."""
        if not fnmatch.fnmatch(command, rule.pattern):
            return False

        # Check ``after`` conditional
        if rule.after is not None:
            if not any(
                fnmatch.fnmatch(prev, rule.after)
                for prev in self._command_history
            ):
                return False

        return True

    @property
    def all_rules(self) -> list[PolicyRule]:
        """All rules in evaluation order (amendments → project → builtin)."""
        return self._session_amendments + self._project_rules + self._builtin_rules


# ---------------------------------------------------------------------------
# Rules file parser
# ---------------------------------------------------------------------------

def parse_rules_file(path: Path) -> list[PolicyRule]:
    """Parse a .rules file into PolicyRule list.

    Format: blocks separated by blank lines.  Each block has key = value
    lines.  Required: ``pattern``, ``decision``.  Optional: ``after``,
    ``reason``.
    """
    if not path.is_file():
        return []

    rules: list[PolicyRule] = []
    current: dict[str, str] = {}

    def _flush() -> None:
        if "pattern" in current and "decision" in current:
            decision = current["decision"]
            rules.append(PolicyRule(
                pattern=current["pattern"],
                decision=decision,
                after=current.get("after"),
                reason=current.get("reason", ""),
                immutable=(decision == "deny"),
            ))
        elif current:
            logger.warning("Skipping malformed rule block: %s", current)
        current.clear()

    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()

        # Skip comments and empty lines (block separator)
        if stripped.startswith("#") or not stripped:
            if not stripped:
                _flush()
            continue

        if "=" in stripped:
            key, _, value = stripped.partition("=")
            current[key.strip()] = value.strip()

    _flush()
    return rules


def load_default_policy(project_path: Path | None = None) -> ExecPolicy:
    """Load the default execution policy with built-in + project rules.

    Built-in rules from ``exec_rules/default.rules``.
    Project rules from ``.llm-code/exec.rules`` (if present).
    """
    # Built-in rules
    builtin_path = Path(__file__).parent / "exec_rules" / "default.rules"
    builtin_rules = parse_rules_file(builtin_path)

    # Project rules
    project_rules: list[PolicyRule] = []
    if project_path is not None:
        project_rules_path = project_path / ".llm-code" / "exec.rules"
        project_rules = parse_rules_file(project_rules_path)

    return ExecPolicy(
        _builtin_rules=builtin_rules,
        _project_rules=project_rules,
    )
