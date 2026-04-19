"""Route model IDs to the right family-specific system prompt.

Reference-aligned with opencode (packages/opencode/src/session/prompt/):
opencode added three model-specific variants beyond the plain ``gpt``
family — ``beast``, ``copilot-gpt-5``, ``trinity``. We add the same.

    * ``beast``         — OpenAI's reasoning models (o1 / o3 / gpt-4 /
      gpt-5). They reward long autonomous iteration + explicit
      planning, so the baseline ``gpt`` prompt under-delivers.
    * ``copilot-gpt-5`` — GitHub Copilot's GPT-5 backend. Ships under a
      different API surface / tool-call dialect and is worth a tuned
      prompt.
    * ``trinity``       — Catch-all for models that self-identify as
      ``trinity``. Tracks opencode's convention.

Routing is *ordered* — more specific patterns must win over generic
ones (copilot before gpt, trinity before default, beast before gpt).
"""
from __future__ import annotations

from llm_code.runtime.prompt import select_intro_prompt


def _marker(name: str) -> str:
    """Return a tiny substring only found in prompts/<name>.md.

    Lets us assert routing without pinning the test to the exact
    prompt body.
    """
    return _MARKERS[name]


_MARKERS = {
    "beast": "# Beast",
    "copilot_gpt5": "# Copilot GPT-5",
    "trinity": "# Trinity",
}


class TestBeastRouting:
    def test_o1_routes_to_beast(self) -> None:
        assert _marker("beast") in select_intro_prompt("o1")
        assert _marker("beast") in select_intro_prompt("o1-preview")

    def test_o3_routes_to_beast(self) -> None:
        assert _marker("beast") in select_intro_prompt("o3-mini")

    def test_gpt4_routes_to_beast(self) -> None:
        assert _marker("beast") in select_intro_prompt("gpt-4")
        assert _marker("beast") in select_intro_prompt("gpt-4o")
        assert _marker("beast") in select_intro_prompt("gpt-4-turbo")

    def test_gpt5_routes_to_beast(self) -> None:
        assert _marker("beast") in select_intro_prompt("gpt-5")

    def test_plain_gpt_still_routes_to_gpt(self) -> None:
        """gpt-3.5 / gpt-plain should stay on the normal gpt prompt —
        only reasoning-class OpenAI models go to beast."""
        out = select_intro_prompt("gpt-3.5-turbo")
        assert _marker("beast") not in out
        assert "powered by a GPT model" in out  # existing gpt.md intro


class TestCopilotRouting:
    def test_copilot_gpt5_routes_to_copilot_prompt(self) -> None:
        assert _marker("copilot_gpt5") in select_intro_prompt("copilot-gpt-5")

    def test_generic_copilot_routes_to_copilot_prompt(self) -> None:
        """Any model id containing ``copilot`` takes the copilot path —
        it's a distinct backend, not just 'a GPT variant'."""
        assert _marker("copilot_gpt5") in select_intro_prompt("github-copilot")

    def test_copilot_beats_beast(self) -> None:
        """``copilot-gpt-5`` contains both ``copilot`` and ``gpt-5`` —
        copilot must win because the Copilot surface differs in ways
        the beast prompt doesn't cover."""
        out = select_intro_prompt("copilot-gpt-5")
        assert _marker("copilot_gpt5") in out
        assert _marker("beast") not in out


class TestTrinityRouting:
    def test_trinity_substring_routes_to_trinity(self) -> None:
        assert _marker("trinity") in select_intro_prompt("trinity-preview")

    def test_trinity_beats_default(self) -> None:
        out = select_intro_prompt("trinity")
        assert _marker("trinity") in out


class TestExistingRoutingUntouched:
    """Smoke-check that the new branches don't shadow existing ones."""

    def test_claude_still_routes_to_anthropic(self) -> None:
        out = select_intro_prompt("claude-sonnet-4")
        assert _marker("beast") not in out
        assert _marker("copilot_gpt5") not in out

    def test_gemini_still_routes_to_gemini(self) -> None:
        out = select_intro_prompt("gemini-pro")
        assert _marker("beast") not in out

    def test_qwen_still_routes_to_qwen(self) -> None:
        out = select_intro_prompt("qwen2.5-coder")
        assert _marker("beast") not in out

    def test_unknown_model_falls_back_to_default(self) -> None:
        out = select_intro_prompt("some-new-model-nobody-heard-of")
        assert _marker("beast") not in out
        assert _marker("copilot_gpt5") not in out
        assert _marker("trinity") not in out
