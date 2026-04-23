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


class TestGLMRouting:
    """GLM (Zhipu) — reasoning lives in `reasoning_content`, so the
    model-specific prompt warns against leaking the chain-of-thought
    into the answer channel (see v2.2.1 issue surfaced post-v2.2.0 GA).
    """

    _GLM_MARKER = "powered by GLM (Zhipu)"

    def test_glm_5_routes_to_glm(self) -> None:
        out = select_intro_prompt("glm-5.1")
        assert self._GLM_MARKER in out

    def test_glm_4_routes_to_glm(self) -> None:
        out = select_intro_prompt("glm-4-plus")
        assert self._GLM_MARKER in out

    def test_zhipu_alias_routes_to_glm(self) -> None:
        out = select_intro_prompt("zhipu-chatglm")
        assert self._GLM_MARKER in out

    def test_glm_case_insensitive(self) -> None:
        out = select_intro_prompt("GLM-5.1")
        assert self._GLM_MARKER in out

    def test_glm_does_not_shadow_qwen(self) -> None:
        """Defensive: 'qwen' containing 'glm' substring shouldn't
        happen, but assert the two routes are distinct."""
        qwen_out = select_intro_prompt("qwen3.5-plus")
        assert self._GLM_MARKER not in qwen_out

    def test_glm_prompt_warns_against_reasoning_leak(self) -> None:
        """Plan #GLM — the template must explicitly forbid meta-
        narration in the answer channel (mitigation for the
        v2.2.0-GA screenshot where GLM leaked English thinking
        before the Chinese answer)."""
        out = select_intro_prompt("glm-5.1")
        assert "reasoning_content" in out
        assert "NEVER put reasoning text into `content`" in out

    def test_glm_prompt_emphasises_web_search_for_real_time(self) -> None:
        """Plan #GLM — GLM must be told explicitly that `web_search`
        is the right tool for real-time queries so it stops refusing
        'today's news' style asks by citing its training cutoff."""
        out = select_intro_prompt("glm-5.1")
        assert "web_search" in out
        assert "today's news" in out
