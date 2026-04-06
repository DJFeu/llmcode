"""Tests for user-defined bash rules in classify_command()."""
from __future__ import annotations

from llm_code.tools.bash import classify_command
from llm_code.runtime.config import BashRule


class TestUserBashRules:
    def test_user_allow_rule(self):
        rules = (BashRule(pattern=r"^git\s+push\b(?!.*--force)", action="allow"),)
        result = classify_command("git push origin main", user_rules=rules)
        assert result.is_safe
        assert "user:0" in result.rule_ids

    def test_user_block_rule(self):
        rules = (BashRule(pattern=r"^docker\s+system\s+prune", action="block"),)
        result = classify_command("docker system prune", user_rules=rules)
        assert result.is_blocked
        assert "user:0" in result.rule_ids

    def test_user_confirm_rule(self):
        rules = (BashRule(pattern=r"^git\s+push\s+--force", action="confirm"),)
        result = classify_command("git push --force origin main", user_rules=rules)
        assert result.needs_confirm
        assert "user:0" in result.rule_ids

    def test_user_rules_take_precedence(self):
        """User 'allow' overrides built-in classification."""
        rules = (BashRule(pattern=r"^rm\s+temp\.txt$", action="allow"),)
        result = classify_command("rm temp.txt", user_rules=rules)
        assert result.is_safe

    def test_no_user_rules_falls_through(self):
        result = classify_command("ls -la", user_rules=())
        assert result.is_safe

    def test_first_matching_rule_wins(self):
        rules = (
            BashRule(pattern=r"^git\s+push", action="block"),
            BashRule(pattern=r"^git\s+push", action="allow"),
        )
        result = classify_command("git push", user_rules=rules)
        assert result.is_blocked

    def test_invalid_regex_skipped(self):
        rules = (BashRule(pattern=r"[invalid", action="allow"),)
        result = classify_command("ls", user_rules=rules)
        assert result.is_safe  # Falls through to built-in
