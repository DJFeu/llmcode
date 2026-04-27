"""v2.9.0 P3 — final compile thinking=0 heuristic tests.

The "compile" step is iter N+1 after the model has issued >= N tool
calls in this turn. By that point the model's job is to summarise N
tool results into one user-facing answer; deep chain-of-thought adds
no signal but burns 5-15s of wall-clock on slow local servers. P3
drops the thinking budget to ``profile.compile_thinking_budget``
(typically 0) when the threshold is crossed.

These tests pin down:

* ``compile_after_tool_calls = 0`` is the disable sentinel — v2.8.1
  byte-parity preserved.
* The threshold compares ``tool_calls_this_turn >= compile_after_tool_calls``
  — so threshold=3 fires when 3 calls have happened, not 4.
* ``compile_thinking_budget = 0`` produces a fully disabled thinking
  block (no budget, no enable=True flag).
* Compile heuristic supersedes the v2.8.1 ``post_tool_thinking_budget``
  when both opt in.
* Iteration 0 (first decision call) is unaffected regardless of any
  flag combination.
* Profile schema round-trips the new ``[tool_consumption]`` keys.
"""
from __future__ import annotations

from llm_code.runtime.config import ThinkingConfig
from llm_code.runtime.conversation import build_thinking_extra_body
from llm_code.runtime.model_profile import ModelProfile, _profile_from_dict


def _glm_profile_with_compile_lever(
    *,
    default_budget: int = 16384,
    post_tool_budget: int = 1024,
    compile_after: int = 3,
    compile_budget: int = 0,
) -> ModelProfile:
    """GLM-5.1 profile parameterised for compile-thinking tests."""
    return ModelProfile(
        name="GLM-5.1",
        provider_type="openai-compat",
        native_tools=False,
        supports_reasoning=True,
        force_xml_tools=True,
        implicit_thinking=False,
        reasoning_field="reasoning_content",
        thinking_extra_body_format="chat_template_kwargs",
        default_thinking_budget=default_budget,
        post_tool_thinking_budget=post_tool_budget,
        compile_after_tool_calls=compile_after,
        compile_thinking_budget=compile_budget,
        default_temperature=0.6,
        is_local=True,
        max_output_tokens=8192,
    )


# ── Activation gate ──────────────────────────────────────────────────


class TestCompileActivation:
    """The compile heuristic engages only when all preconditions match."""

    def test_threshold_zero_disables_lever(self) -> None:
        """``compile_after_tool_calls = 0`` is the dataclass default
        and the disable sentinel — even after many tool calls the
        v2.8.1 ``post_tool_thinking_budget`` stays in effect."""
        cfg = ThinkingConfig(mode="enabled", budget_tokens=10000)
        profile = _glm_profile_with_compile_lever(
            compile_after=0, compile_budget=0,
        )
        body = build_thinking_extra_body(
            cfg,
            is_local=True,
            provider_supports_reasoning=True,
            max_output_tokens=8192,
            profile=profile,
            post_tool_iteration=True,
            tool_calls_this_turn=99,  # way over any threshold
        )
        assert body is not None
        # post_tool_thinking_budget=1024 wins because compile is off.
        assert body["chat_template_kwargs"]["thinking_budget"] == 1024

    def test_threshold_not_yet_reached(self) -> None:
        """``compile_after_tool_calls=3, tool_calls_this_turn=2`` —
        not yet engaged. Falls back to v2.8.1 post_tool budget.
        """
        cfg = ThinkingConfig(mode="enabled", budget_tokens=10000)
        profile = _glm_profile_with_compile_lever(
            compile_after=3, compile_budget=0,
        )
        body = build_thinking_extra_body(
            cfg,
            is_local=True,
            provider_supports_reasoning=True,
            max_output_tokens=8192,
            profile=profile,
            post_tool_iteration=True,
            tool_calls_this_turn=2,  # one short of threshold
        )
        assert body is not None
        assert body["chat_template_kwargs"]["thinking_budget"] == 1024

    def test_threshold_exactly_reached(self) -> None:
        """``compile_after_tool_calls=3, tool_calls_this_turn=3`` —
        the comparison is ``>=``, so the lever fires here.
        """
        cfg = ThinkingConfig(mode="enabled", budget_tokens=10000)
        profile = _glm_profile_with_compile_lever(
            compile_after=3, compile_budget=0,
        )
        body = build_thinking_extra_body(
            cfg,
            is_local=True,
            provider_supports_reasoning=True,
            max_output_tokens=8192,
            profile=profile,
            post_tool_iteration=True,
            tool_calls_this_turn=3,  # threshold met
        )
        assert body is not None
        # compile_thinking_budget=0 → fully disabled thinking shape.
        assert body["chat_template_kwargs"]["enable_thinking"] is False
        assert "thinking_budget" not in body["chat_template_kwargs"]

    def test_iteration_zero_unaffected(self) -> None:
        """Iteration 0 (decision phase) keeps full reasoning even
        when the heuristic is opted in. Without this we'd lose the
        depth needed to pick which tool to call next.
        """
        cfg = ThinkingConfig(mode="enabled", budget_tokens=10000)
        profile = _glm_profile_with_compile_lever(
            compile_after=3, compile_budget=0,
        )
        body = build_thinking_extra_body(
            cfg,
            is_local=True,
            provider_supports_reasoning=True,
            max_output_tokens=8192,
            profile=profile,
            post_tool_iteration=False,  # iteration 0
            tool_calls_this_turn=0,
        )
        assert body is not None
        # Full default budget on iteration 0 regardless of any flag.
        assert body["chat_template_kwargs"]["thinking_budget"] == 16384


# ── Compile budget shape ─────────────────────────────────────────────


class TestCompileBudgetShape:
    """``compile_thinking_budget = 0`` → disabled config, not enabled-with-zero."""

    def test_zero_compile_budget_disables_thinking(self) -> None:
        cfg = ThinkingConfig(mode="enabled", budget_tokens=10000)
        profile = _glm_profile_with_compile_lever(
            compile_after=3, compile_budget=0,
        )
        body = build_thinking_extra_body(
            cfg,
            is_local=True,
            provider_supports_reasoning=True,
            max_output_tokens=8192,
            profile=profile,
            post_tool_iteration=True,
            tool_calls_this_turn=3,
        )
        assert body is not None
        # Disabled-shape: openai-compat → enable_thinking=false, no budget key.
        ctk = body["chat_template_kwargs"]
        assert ctk == {"enable_thinking": False}

    def test_nonzero_compile_budget_still_enables_thinking(self) -> None:
        """If a profile sets ``compile_thinking_budget = 256`` (some
        reasoning, less than the 1024 post-tool budget), the lever
        engages but thinking is still on with that smaller budget.
        """
        cfg = ThinkingConfig(mode="enabled", budget_tokens=10000)
        profile = _glm_profile_with_compile_lever(
            compile_after=3, compile_budget=256,
        )
        body = build_thinking_extra_body(
            cfg,
            is_local=True,
            provider_supports_reasoning=True,
            max_output_tokens=8192,
            profile=profile,
            post_tool_iteration=True,
            tool_calls_this_turn=3,
        )
        assert body is not None
        assert body["chat_template_kwargs"]["enable_thinking"] is True
        assert body["chat_template_kwargs"]["thinking_budget"] == 256


# ── Anthropic shape ──────────────────────────────────────────────────


class TestCompileBudgetAnthropicShape:
    """Anthropic-native thinking format also produces the disabled
    block when compile_budget = 0."""

    def test_anthropic_disabled_block(self) -> None:
        cfg = ThinkingConfig(mode="enabled", budget_tokens=10000)
        profile = ModelProfile(
            name="claude-test",
            provider_type="anthropic",
            supports_reasoning=True,
            thinking_extra_body_format="anthropic_native",
            default_thinking_budget=10000,
            post_tool_thinking_budget=2048,
            compile_after_tool_calls=2,
            compile_thinking_budget=0,
            is_local=False,
        )
        body = build_thinking_extra_body(
            cfg,
            is_local=False,
            provider_supports_reasoning=True,
            max_output_tokens=8192,
            profile=profile,
            post_tool_iteration=True,
            tool_calls_this_turn=2,
        )
        assert body is not None
        assert body == {"thinking": {"type": "disabled"}}


# ── Integration with v2.8.1 post-tool budget ─────────────────────────


class TestCompileSupersedePostTool:
    """Compile lever wins over v2.8.1 ``post_tool_thinking_budget``
    when both opt in.
    """

    def test_compile_engaged_overrides_post_tool_budget(self) -> None:
        """At the threshold, compile_thinking_budget wins. v2.8.1's
        post_tool_thinking_budget is ignored even though it's set."""
        cfg = ThinkingConfig(mode="enabled", budget_tokens=10000)
        profile = _glm_profile_with_compile_lever(
            post_tool_budget=1024,
            compile_after=3,
            compile_budget=0,
        )
        body = build_thinking_extra_body(
            cfg,
            is_local=True,
            provider_supports_reasoning=True,
            max_output_tokens=8192,
            profile=profile,
            post_tool_iteration=True,
            tool_calls_this_turn=5,  # well past threshold
        )
        assert body is not None
        # Disabled shape, not 1024, not 16384.
        assert body["chat_template_kwargs"] == {"enable_thinking": False}


# ── Backwards compat ─────────────────────────────────────────────────


class TestBackcompat:
    """Calls without the new ``tool_calls_this_turn`` kwarg keep
    working — the parameter has a default so existing callers don't
    need to change.
    """

    def test_default_kwarg_no_compile_engagement(self) -> None:
        """Calling without ``tool_calls_this_turn`` defaults to 0,
        which means the threshold (>0) is never met — v2.8.1
        behaviour preserved.
        """
        cfg = ThinkingConfig(mode="enabled", budget_tokens=10000)
        profile = _glm_profile_with_compile_lever(
            compile_after=3, compile_budget=0,
        )
        body = build_thinking_extra_body(
            cfg,
            is_local=True,
            provider_supports_reasoning=True,
            max_output_tokens=8192,
            profile=profile,
            post_tool_iteration=True,
            # tool_calls_this_turn omitted — default is 0
        )
        assert body is not None
        # Falls back to post_tool_budget=1024.
        assert body["chat_template_kwargs"]["thinking_budget"] == 1024


# ── Profile schema round-trip ────────────────────────────────────────


class TestProfileSchemaRoundtrip:
    """The new ``[tool_consumption]`` keys parse cleanly."""

    def test_toml_loads_compile_fields(self) -> None:
        raw = {
            "name": "x",
            "tool_consumption": {
                "compile_after_tool_calls": 3,
                "compile_thinking_budget": 0,
            },
        }
        profile = _profile_from_dict(raw)
        assert profile.compile_after_tool_calls == 3
        assert profile.compile_thinking_budget == 0

    def test_toml_defaults_when_omitted(self) -> None:
        raw = {"name": "legacy"}
        profile = _profile_from_dict(raw)
        assert profile.compile_after_tool_calls == 0
        assert profile.compile_thinking_budget == 0
