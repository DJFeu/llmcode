"""v2.8.1 — per-iteration thinking budget for post-tool consumption.

When a turn dispatches a tool, iteration 0 (decision phase) needs
full thinking to pick the tool + shape args, but iteration 1+ is
just summarising the tool result that's already ground truth — deep
reasoning at that phase wastes 30-90s of wall clock on slow local
models. ``post_tool_thinking_budget`` overrides
``default_thinking_budget`` for those iterations only.

Tests pin down:
- Override applies only when ``post_tool_iteration=True``
- Override applies only when profile pins a non-zero override AND
  ``default_thinking_budget`` is also non-zero (no override path
  for profiles that already use the legacy adaptive flow)
- Iteration 0 still uses the full ``default_thinking_budget``
- Override = 0 (dataclass default) preserves v2.8.0 byte-for-byte
- Reasoning-effort scaling still applies on top of the override
"""
from __future__ import annotations

from llm_code.runtime.config import ThinkingConfig
from llm_code.runtime.conversation import build_thinking_extra_body
from llm_code.runtime.model_profile import ModelProfile


def _glm_profile_with_post_tool_cap(post_tool_budget: int = 1024) -> ModelProfile:
    """GLM-5.1 profile with the v2.8.1 post-tool override field set."""
    return ModelProfile(
        name="GLM-5.1 (744B.A40B)",
        provider_type="openai-compat",
        native_tools=False,
        supports_reasoning=True,
        force_xml_tools=True,
        implicit_thinking=False,
        reasoning_field="reasoning_content",
        thinking_extra_body_format="chat_template_kwargs",
        default_thinking_budget=16384,
        post_tool_thinking_budget=post_tool_budget,
        default_temperature=0.6,
        reasoning_effort="",  # leave at default — no scaling
        is_local=True,
        max_output_tokens=8192,
    )


class TestPostToolBudgetActivation:
    """Override fires only on the right iteration shape."""

    def test_iteration_zero_uses_full_default_budget(self) -> None:
        """Decision phase (iteration 0) keeps the full
        ``default_thinking_budget`` even when the override is set.
        Without this, the model loses the reasoning depth needed to
        decide WHICH tool to call."""
        cfg = ThinkingConfig(mode="enabled", budget_tokens=10000)
        body = build_thinking_extra_body(
            cfg,
            is_local=True,
            provider_supports_reasoning=True,
            max_output_tokens=8192,
            profile=_glm_profile_with_post_tool_cap(post_tool_budget=1024),
            post_tool_iteration=False,  # iteration 0
        )
        assert body is not None
        budget = body["chat_template_kwargs"]["thinking_budget"]
        assert budget == 16384, (
            f"iteration 0 must use default 16384; got {budget}"
        )

    def test_post_tool_iteration_uses_reduced_budget(self) -> None:
        """Iteration 1+ on a turn that dispatched a tool gets the
        reduced budget — the actual leverage of v2.8.1."""
        cfg = ThinkingConfig(mode="enabled", budget_tokens=10000)
        body = build_thinking_extra_body(
            cfg,
            is_local=True,
            provider_supports_reasoning=True,
            max_output_tokens=8192,
            profile=_glm_profile_with_post_tool_cap(post_tool_budget=1024),
            post_tool_iteration=True,
        )
        assert body is not None
        budget = body["chat_template_kwargs"]["thinking_budget"]
        assert budget == 1024, (
            f"post-tool iteration must use override 1024; got {budget}"
        )

    def test_zero_override_preserves_v280_behaviour(self) -> None:
        """``post_tool_thinking_budget == 0`` is the dataclass default.
        Profile keeps full ``default_thinking_budget`` on every
        iteration (v2.8.0 byte-for-byte) — no override path engages."""
        cfg = ThinkingConfig(mode="enabled", budget_tokens=10000)
        profile = _glm_profile_with_post_tool_cap(post_tool_budget=0)
        body = build_thinking_extra_body(
            cfg,
            is_local=True,
            provider_supports_reasoning=True,
            max_output_tokens=8192,
            profile=profile,
            post_tool_iteration=True,
        )
        assert body is not None
        budget = body["chat_template_kwargs"]["thinking_budget"]
        assert budget == 16384, (
            "profiles without the v2.8.1 opt-in must keep the full "
            "16384 budget on every iteration"
        )

    def test_default_budget_zero_disables_override_too(self) -> None:
        """If a profile has no ``default_thinking_budget`` (legacy
        adaptive flow), the post-tool override does NOT engage —
        switching budgets mid-stream when the profile didn't pin one
        in the first place would be unexpected."""
        cfg = ThinkingConfig(mode="enabled", budget_tokens=10000)
        profile = ModelProfile(
            name="legacy",
            provider_type="openai-compat",
            supports_reasoning=True,
            implicit_thinking=False,
            thinking_extra_body_format="chat_template_kwargs",
            default_thinking_budget=0,  # legacy adaptive
            post_tool_thinking_budget=512,  # opt-in but ignored
            is_local=True,
        )
        body = build_thinking_extra_body(
            cfg,
            is_local=True,
            provider_supports_reasoning=True,
            max_output_tokens=8192,
            profile=profile,
            post_tool_iteration=True,
        )
        # Adaptive path stays engaged because profile_budget == 0.
        assert body is not None
        # Validate the override didn't sneak in: budget should NOT be
        # 512 (the override value); it should reflect the legacy
        # config.budget_tokens path.
        budget = body["chat_template_kwargs"]["thinking_budget"]
        assert budget != 512, (
            "post-tool override must not engage when the profile "
            "lacks a default_thinking_budget"
        )


class TestPostToolBudgetInteractsWithEffort:
    """Reasoning-effort scaling still applies on top of the override."""

    def test_low_effort_scales_post_tool_budget(self) -> None:
        cfg = ThinkingConfig(mode="enabled", budget_tokens=10000)
        profile = ModelProfile(
            name="x",
            provider_type="openai-compat",
            supports_reasoning=True,
            thinking_extra_body_format="chat_template_kwargs",
            default_thinking_budget=16384,
            post_tool_thinking_budget=2048,
            reasoning_effort="low",  # 0.25× scale
            is_local=True,
        )
        body = build_thinking_extra_body(
            cfg,
            is_local=True,
            provider_supports_reasoning=True,
            max_output_tokens=8192,
            profile=profile,
            post_tool_iteration=True,
        )
        assert body is not None
        # 2048 × 0.25 = 512 — effort scaling is applied to the override.
        budget = body["chat_template_kwargs"]["thinking_budget"]
        assert budget == 512, (
            f"reasoning_effort=low must scale post-tool budget; got {budget}"
        )


class TestPostToolBudgetBackcompat:
    """v2.8.0 callers without the new kwarg still work."""

    def test_call_without_post_tool_iteration_kwarg(self) -> None:
        """The new parameter has a default of False so existing
        callers (tests, internal users) don't need to change."""
        cfg = ThinkingConfig(mode="enabled", budget_tokens=10000)
        profile = _glm_profile_with_post_tool_cap(post_tool_budget=1024)
        # Call without the new kwarg (i.e. like v2.8.0 did).
        body = build_thinking_extra_body(
            cfg,
            is_local=True,
            provider_supports_reasoning=True,
            max_output_tokens=8192,
            profile=profile,
        )
        assert body is not None
        # Default = False = iteration 0 path = full 16384 budget.
        budget = body["chat_template_kwargs"]["thinking_budget"]
        assert budget == 16384, (
            "missing kwarg must default to iteration 0 path"
        )


class TestProfileSchemaRoundtrip:
    """``[thinking] post_tool_thinking_budget`` parses from TOML."""

    def test_toml_section_loads_field(self, tmp_path) -> None:
        from llm_code.runtime.model_profile import _profile_from_dict
        raw = {
            "name": "test",
            "thinking": {
                "default_thinking_budget": 16384,
                "post_tool_thinking_budget": 1024,
            },
        }
        profile = _profile_from_dict(raw)
        assert profile.default_thinking_budget == 16384
        assert profile.post_tool_thinking_budget == 1024

    def test_toml_omitting_post_tool_field_defaults_to_zero(self) -> None:
        from llm_code.runtime.model_profile import _profile_from_dict
        raw = {
            "name": "legacy",
            "thinking": {"default_thinking_budget": 16384},
        }
        profile = _profile_from_dict(raw)
        assert profile.post_tool_thinking_budget == 0
