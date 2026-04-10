"""Tests for execution policy engine."""
from __future__ import annotations

from pathlib import Path

from llm_code.runtime.exec_policy import (
    ExecPolicy,
    PolicyRule,
    load_default_policy,
    parse_rules_file,
)


class TestPolicyRule:
    def test_deny_is_immutable(self) -> None:
        rule = PolicyRule(pattern="rm -rf *", decision="deny", immutable=True)
        assert rule.immutable is True

    def test_allow_is_mutable(self) -> None:
        rule = PolicyRule(pattern="cat *", decision="allow")
        assert rule.immutable is False


class TestExecPolicy:
    def _make_policy(self, rules: list[PolicyRule]) -> ExecPolicy:
        return ExecPolicy(_builtin_rules=rules)

    def test_basic_allow(self) -> None:
        policy = self._make_policy([
            PolicyRule(pattern="cat *", decision="allow"),
        ])
        decision, _ = policy.evaluate("cat README.md")
        assert decision == "allow"

    def test_basic_deny(self) -> None:
        policy = self._make_policy([
            PolicyRule(pattern="rm -rf /*", decision="deny", reason="dangerous", immutable=True),
        ])
        decision, reason = policy.evaluate("rm -rf /")
        assert decision == "deny"
        assert reason == "dangerous"

    def test_fallthrough_is_prompt(self) -> None:
        policy = self._make_policy([])
        decision, _ = policy.evaluate("some-unknown-command")
        assert decision == "prompt"

    def test_first_match_wins(self) -> None:
        policy = self._make_policy([
            PolicyRule(pattern="git *", decision="allow"),
            PolicyRule(pattern="git push*", decision="prompt"),
        ])
        decision, _ = policy.evaluate("git push origin main")
        assert decision == "allow"  # first rule matches

    def test_after_conditional(self) -> None:
        policy = self._make_policy([
            PolicyRule(pattern="git commit*", decision="allow", after="git add*"),
        ])
        # Without prerequisite → fallthrough
        decision, _ = policy.evaluate("git commit -m 'test'")
        assert decision == "prompt"

        # With prerequisite
        policy.record_command("git add .")
        decision, _ = policy.evaluate("git commit -m 'test'")
        assert decision == "allow"

    def test_session_amendment(self) -> None:
        policy = self._make_policy([])
        policy.amend(PolicyRule(pattern="npm test*", decision="allow"))
        decision, _ = policy.evaluate("npm test --coverage")
        assert decision == "allow"

    def test_amendment_rejected_on_immutable_conflict(self) -> None:
        policy = self._make_policy([
            PolicyRule(pattern="rm -rf *", decision="deny", immutable=True),
        ])
        ok = policy.amend(PolicyRule(pattern="rm -rf *", decision="allow"))
        assert ok is False

    def test_amendment_takes_precedence(self) -> None:
        policy = self._make_policy([
            PolicyRule(pattern="docker *", decision="prompt"),
        ])
        policy.amend(PolicyRule(pattern="docker build*", decision="allow"))
        decision, _ = policy.evaluate("docker build .")
        assert decision == "allow"  # amendment > builtin

    def test_command_history_limit(self) -> None:
        policy = self._make_policy([])
        for i in range(60):
            policy.record_command(f"cmd-{i}")
        assert len(policy._command_history) == 50

    def test_project_rules_layer(self) -> None:
        policy = ExecPolicy(
            _builtin_rules=[PolicyRule(pattern="make *", decision="prompt")],
            _project_rules=[PolicyRule(pattern="make test", decision="allow")],
        )
        assert policy.evaluate("make test")[0] == "allow"  # project > builtin
        assert policy.evaluate("make deploy")[0] == "prompt"  # falls to builtin


class TestParseRulesFile:
    def test_parse_valid(self, tmp_path: Path) -> None:
        rules_file = tmp_path / "test.rules"
        rules_file.write_text(
            "pattern = cat *\n"
            "decision = allow\n"
            "\n"
            "pattern = rm -rf /*\n"
            "decision = deny\n"
            "reason = dangerous\n"
        )
        rules = parse_rules_file(rules_file)
        assert len(rules) == 2
        assert rules[0].pattern == "cat *"
        assert rules[0].decision == "allow"
        assert rules[1].decision == "deny"
        assert rules[1].immutable is True

    def test_parse_with_after(self, tmp_path: Path) -> None:
        rules_file = tmp_path / "test.rules"
        rules_file.write_text(
            "pattern = git commit*\n"
            "after = git add*\n"
            "decision = allow\n"
        )
        rules = parse_rules_file(rules_file)
        assert len(rules) == 1
        assert rules[0].after == "git add*"

    def test_parse_with_comments(self, tmp_path: Path) -> None:
        rules_file = tmp_path / "test.rules"
        rules_file.write_text(
            "# This is a comment\n"
            "pattern = cat *\n"
            "decision = allow\n"
        )
        rules = parse_rules_file(rules_file)
        assert len(rules) == 1

    def test_parse_nonexistent(self) -> None:
        rules = parse_rules_file(Path("/nonexistent"))
        assert rules == []

    def test_malformed_skipped(self, tmp_path: Path) -> None:
        rules_file = tmp_path / "test.rules"
        rules_file.write_text(
            "pattern = cat *\n"
            "\n"
            "decision = allow\n"  # missing pattern
        )
        rules = parse_rules_file(rules_file)
        # First block has pattern but no decision → skipped
        # Second block has decision but no pattern → skipped
        assert len(rules) == 0


class TestLoadDefaultPolicy:
    def test_loads_builtin_rules(self) -> None:
        policy = load_default_policy()
        assert len(policy._builtin_rules) > 0
        # Should have both allow and deny rules
        decisions = {r.decision for r in policy._builtin_rules}
        assert "allow" in decisions
        assert "deny" in decisions

    def test_deny_rules_are_immutable(self) -> None:
        policy = load_default_policy()
        for rule in policy._builtin_rules:
            if rule.decision == "deny":
                assert rule.immutable is True

    def test_cat_is_allowed(self) -> None:
        policy = load_default_policy()
        decision, _ = policy.evaluate("cat README.md")
        assert decision == "allow"

    def test_rm_rf_root_denied(self) -> None:
        policy = load_default_policy()
        decision, _ = policy.evaluate("rm -rf /")
        assert decision == "deny"

    def test_unknown_command_prompts(self) -> None:
        policy = load_default_policy()
        decision, _ = policy.evaluate("some-novel-command --flag")
        assert decision == "prompt"

    def test_pytest_allowed(self) -> None:
        policy = load_default_policy()
        decision, _ = policy.evaluate("pytest tests/ -v")
        assert decision == "allow"

    def test_git_status_allowed(self) -> None:
        policy = load_default_policy()
        decision, _ = policy.evaluate("git status")
        assert decision == "allow"
