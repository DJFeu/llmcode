"""Tests for composable system prompt snippets."""
from __future__ import annotations

from llm_code.runtime.prompt_snippets import (
    BUILTIN_SNIPPETS,
    PromptSnippet,
    compose_system_prompt,
)


class TestComposeSystemPrompt:
    def test_basic_composition(self) -> None:
        snippets = [
            PromptSnippet(key="a", content="Hello", priority=10),
            PromptSnippet(key="b", content="World", priority=20),
        ]
        result = compose_system_prompt(snippets)
        assert result == "Hello\n\nWorld"

    def test_priority_ordering(self) -> None:
        snippets = [
            PromptSnippet(key="late", content="Second", priority=20),
            PromptSnippet(key="early", content="First", priority=10),
        ]
        result = compose_system_prompt(snippets)
        assert result.startswith("First")

    def test_condition_true(self) -> None:
        snippets = [
            PromptSnippet(key="cond", content="Included",
                          condition=lambda is_local=False, **_: is_local),
        ]
        result = compose_system_prompt(snippets, is_local=True)
        assert "Included" in result

    def test_condition_false(self) -> None:
        snippets = [
            PromptSnippet(key="cond", content="Excluded",
                          condition=lambda is_local=False, **_: is_local),
        ]
        result = compose_system_prompt(snippets, is_local=False)
        assert result == ""

    def test_empty_content_skipped(self) -> None:
        snippets = [
            PromptSnippet(key="empty", content="   "),
            PromptSnippet(key="real", content="Content"),
        ]
        result = compose_system_prompt(snippets)
        assert result == "Content"

    def test_duplicate_key_last_wins(self) -> None:
        snippets = [
            PromptSnippet(key="x", content="First version"),
            PromptSnippet(key="x", content="Second version"),
        ]
        result = compose_system_prompt(snippets)
        assert result == "Second version"

    def test_condition_exception_skips(self) -> None:
        def bad_condition(**_):
            raise RuntimeError("boom")

        snippets = [
            PromptSnippet(key="bad", content="Should skip", condition=bad_condition),
            PromptSnippet(key="good", content="Included"),
        ]
        result = compose_system_prompt(snippets)
        assert result == "Included"

    def test_no_snippets(self) -> None:
        assert compose_system_prompt([]) == ""

    def test_builtin_snippets_compose(self) -> None:
        result = compose_system_prompt(BUILTIN_SNIPPETS, is_local=False, force_xml=False)
        assert "coding assistant" in result
        assert "Rules:" in result
        # Local-only snippets should NOT be present
        assert "Do NOT use the agent tool" not in result

    def test_builtin_with_local_model(self) -> None:
        result = compose_system_prompt(BUILTIN_SNIPPETS, is_local=True, force_xml=False)
        assert "Do NOT use the agent tool" in result
        assert "MUST produce a substantive response" in result

    def test_builtin_with_xml_mode(self) -> None:
        result = compose_system_prompt(BUILTIN_SNIPPETS, is_local=False, force_xml=True)
        assert "<tool_call>" in result

    def test_custom_snippet_mixed_with_builtin(self) -> None:
        custom = PromptSnippet(key="project_rules", content="Follow PEP 8.", priority=15)
        all_snippets = BUILTIN_SNIPPETS + [custom]
        result = compose_system_prompt(all_snippets)
        assert "Follow PEP 8." in result
        # Custom at priority 15 should be between intro (10) and behavior (20)
        intro_pos = result.find("coding assistant")
        pep8_pos = result.find("Follow PEP 8.")
        rules_pos = result.find("Rules:")
        assert intro_pos < pep8_pos < rules_pos
