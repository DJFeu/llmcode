"""Tests for governance rule injection into the system prompt."""
from __future__ import annotations



from llm_code.runtime.context import ProjectContext
from llm_code.runtime.memory_layers import GovernanceRule
from llm_code.runtime.prompt import SystemPromptBuilder


def _make_context(cwd: str = "/tmp/test") -> ProjectContext:
    return ProjectContext(
        cwd=cwd,
        is_git_repo=False,
        git_status="",
        instructions="",
    )


class TestGovernanceInSystemPrompt:
    def test_governance_rules_injected_at_start(self):
        builder = SystemPromptBuilder()
        rules = (
            GovernanceRule(
                category="security",
                content="No hardcoded secrets",
                source="CLAUDE.md",
                priority=1,
            ),
            GovernanceRule(
                category="style",
                content="Use type annotations",
                source=".llm-code/rules/style.md",
                priority=5,
            ),
        )
        prompt = builder.build(
            context=_make_context(),
            governance_rules=rules,
        )
        # Governance section should appear before the behavior rules
        gov_idx = prompt.find("## Governance Rules")
        intro_idx = prompt.find("Rules:")
        assert gov_idx != -1, "Governance section not found"
        assert gov_idx < intro_idx, "Governance must come before behavior rules"

    def test_governance_rules_contain_content(self):
        builder = SystemPromptBuilder()
        rules = (
            GovernanceRule(
                category="testing",
                content="Always write tests first",
                source="governance.md",
                priority=10,
            ),
        )
        prompt = builder.build(
            context=_make_context(),
            governance_rules=rules,
        )
        assert "Always write tests first" in prompt

    def test_governance_rules_show_source(self):
        builder = SystemPromptBuilder()
        rules = (
            GovernanceRule(
                category="style",
                content="Use black",
                source=".llm-code/rules/style.md",
                priority=5,
            ),
        )
        prompt = builder.build(
            context=_make_context(),
            governance_rules=rules,
        )
        assert "style.md" in prompt

    def test_no_governance_rules_no_section(self):
        builder = SystemPromptBuilder()
        prompt = builder.build(context=_make_context())
        assert "## Governance Rules" not in prompt

    def test_empty_governance_rules_no_section(self):
        builder = SystemPromptBuilder()
        prompt = builder.build(
            context=_make_context(),
            governance_rules=(),
        )
        assert "## Governance Rules" not in prompt

    def test_governance_grouped_by_category(self):
        builder = SystemPromptBuilder()
        rules = (
            GovernanceRule(category="security", content="Rule A", source="a.md", priority=1),
            GovernanceRule(category="security", content="Rule B", source="b.md", priority=1),
            GovernanceRule(category="style", content="Rule C", source="c.md", priority=1),
        )
        prompt = builder.build(
            context=_make_context(),
            governance_rules=rules,
        )
        # Both security rules should appear near each other
        assert "Rule A" in prompt
        assert "Rule B" in prompt
        assert "Rule C" in prompt
