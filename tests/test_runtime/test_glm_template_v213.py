"""v2.13.0 Lever 1 — GLM parallel-tool prompt nudge tests.

The v2.9.0 P1 ``asyncio.gather`` dispatch path activates only when the
model emits ≥ 2 tool calls in a single assistant response. GLM-5.1
emits one call per iteration in practice, so the gather path stays
dormant. v2.13 Lever 1 nudges the model toward batched emission via a
template-text addition in the GLM ``# Tool call efficiency`` section.

These tests pin down:

* The new instruction text appears in the rendered system prompt for
  the GLM-5.1 profile.
* The substantive talking points (independent pieces, single response,
  concurrent dispatch, N-1 round-trips) are all present so a future
  refactor can't silently drop the wall-clock motivation.
"""
from __future__ import annotations

from llm_code.runtime.context import ProjectContext
from llm_code.runtime.prompt import SystemPromptBuilder


def _ctx() -> ProjectContext:
    return ProjectContext(
        cwd="/tmp/v213-test",
        instructions="",
        is_git_repo=False,
        git_status="",
    )


class TestGlmParallelNudge:
    """The GLM template carries the v2.13 parallel-tool nudge."""

    def test_nudge_paragraph_present_in_rendered_prompt(self) -> None:
        builder = SystemPromptBuilder()
        prompt = builder.build(
            _ctx(),
            model_name="glm-5.1",
            native_tools=False,
            is_local_model=True,
        )
        # Substantive identifying phrase from the new paragraph.
        assert "INDEPENDENT pieces of information" in prompt, (
            "GLM template missing the v2.13 parallel-tool nudge — "
            "look for the 'INDEPENDENT pieces of information' phrase "
            "in glm.j2's '# Tool call efficiency' section."
        )

    def test_nudge_explains_concurrent_dispatch(self) -> None:
        builder = SystemPromptBuilder()
        prompt = builder.build(
            _ctx(),
            model_name="glm-5.1",
            native_tools=False,
            is_local_model=True,
        )
        # The motivation has to be visible — the model should know
        # WHY it's being asked to batch.
        assert "concurrently" in prompt
        assert "round-trips" in prompt

    def test_nudge_mentions_tool_call_block(self) -> None:
        """The nudge specifically tells the model to emit MULTIPLE
        ``<tool_call>`` blocks per response — pin that wording so a
        future refactor doesn't reduce it to vague guidance.
        """
        builder = SystemPromptBuilder()
        prompt = builder.build(
            _ctx(),
            model_name="glm-5.1",
            native_tools=False,
            is_local_model=True,
        )
        assert "<tool_call>" in prompt
        # Sequential-call disclaimer is also part of the nudge so the
        # model doesn't batch dependent calls (which would break the
        # downstream reasoning chain).
        assert "Sequential calls" in prompt or "sequential" in prompt.lower()
