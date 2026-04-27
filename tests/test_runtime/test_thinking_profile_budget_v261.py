"""v2.6.1 M1 — profile.default_thinking_budget routing fix.

Before v2.6.1 the ``ModelProfile.default_thinking_budget`` field was
parsed from TOML but never read by ``build_thinking_extra_body()``.
A GLM-5.1 profile that declared ``default_thinking_budget = 16384``
lost the budget at the runtime boundary: with ``max_tokens=4096``
the cap clipped the budget to ~2048 thinking tokens — far below
the profile's intent. These tests pin down the v2.6.1 fix:

- Profile budget is honored in mode=enabled
- Profile budget is honored in mode=adaptive (local + reasoning)
- Profile budget overrides the user's config.budget_tokens
- Profile budget bypasses the max_output_tokens / 2 cap
- Profile budget = 0 (dataclass default) falls through to the
  v2.6.0 adaptive path
- Profile budget interacts correctly with reasoning_effort scaling
  and the small-model cap

The fix only affects the budget computation — it never enables
thinking on a path that previously disabled it (adaptive + cloud
returns None unchanged).
"""
from __future__ import annotations

from types import SimpleNamespace

from llm_code.runtime.config import ThinkingConfig
from llm_code.runtime.conversation import build_thinking_extra_body
from llm_code.runtime.model_profile import ModelProfile


def _glm_profile() -> ModelProfile:
    """The GLM-5.1 profile shape that triggered the v2.6.1 hotfix."""
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
        default_temperature=0.6,
        reasoning_effort="high",
        is_local=True,
        max_output_tokens=8192,
    )


class TestProfileBudgetEnabledMode:
    """mode=enabled — profile budget is the source of truth."""

    def test_profile_budget_honored_when_enabled(self) -> None:
        cfg = ThinkingConfig(mode="enabled", budget_tokens=10000)
        profile = _glm_profile()
        body = build_thinking_extra_body(
            cfg,
            is_local=True,
            provider_supports_reasoning=True,
            max_output_tokens=8192,
            profile=profile,
        )
        assert body is not None
        assert body["chat_template_kwargs"]["enable_thinking"] is True
        # 16384 from profile, not 10000 from config, not 131072 from local bump
        assert body["chat_template_kwargs"]["thinking_budget"] == 16384

    def test_profile_budget_overrides_config_budget_tokens(self) -> None:
        cfg = ThinkingConfig(mode="enabled", budget_tokens=2048)
        profile = _glm_profile()
        body = build_thinking_extra_body(
            cfg,
            is_local=True,
            provider_supports_reasoning=True,
            max_output_tokens=8192,
            profile=profile,
        )
        # user config (2048) is ignored; profile (16384) wins
        assert body["chat_template_kwargs"]["thinking_budget"] == 16384

    def test_profile_budget_bypasses_max_output_tokens_cap(self) -> None:
        """Original GLM-5.1 bug: max_tokens=4096 clipped budget to 2048.

        With the v2.6.1 fix the cap is bypassed entirely when the
        profile pins the budget — quality stays at the profile's
        declared 16384 even on a tiny output budget.
        """
        cfg = ThinkingConfig(mode="enabled", budget_tokens=10000)
        profile = _glm_profile()
        body = build_thinking_extra_body(
            cfg,
            is_local=True,
            provider_supports_reasoning=True,
            max_output_tokens=4096,
            profile=profile,
        )
        # _apply_thinking_budget_cap would have clipped to max(1024, 4096//2) = 2048
        # The fix bypasses that — profile wins.
        assert body["chat_template_kwargs"]["thinking_budget"] == 16384


class TestProfileBudgetAdaptiveMode:
    """mode=adaptive — profile budget honored on the local+reasoning path."""

    def test_profile_budget_honored_in_adaptive_local_reasoning(self) -> None:
        cfg = ThinkingConfig(mode="adaptive", budget_tokens=10000)
        profile = _glm_profile()
        body = build_thinking_extra_body(
            cfg,
            is_local=True,
            provider_supports_reasoning=True,
            max_output_tokens=8192,
            profile=profile,
        )
        assert body is not None
        assert body["chat_template_kwargs"]["enable_thinking"] is True
        # Without the fix this would have been max(10000, 131072) capped to 4096
        # by max_output_tokens/2. With the fix it honors the profile.
        assert body["chat_template_kwargs"]["thinking_budget"] == 16384

    def test_profile_budget_zero_falls_through_to_legacy_adaptive(self) -> None:
        """default_thinking_budget=0 (dataclass default) preserves v2.6.0 path."""
        cfg = ThinkingConfig(mode="adaptive", budget_tokens=10000)
        profile = ModelProfile(
            name="legacy-no-budget",
            provider_type="openai-compat",
            supports_reasoning=True,
            is_local=True,
            default_thinking_budget=0,  # explicit 0 = "use adaptive"
            max_output_tokens=8192,
        )
        body = build_thinking_extra_body(
            cfg,
            is_local=True,
            provider_supports_reasoning=True,
            max_output_tokens=8192,
            profile=profile,
        )
        # Legacy path: max(10000, 131072) = 131072, then cap at max_output_tokens/2 = 4096
        assert body is not None
        assert body["chat_template_kwargs"]["thinking_budget"] == 4096

    def test_profile_budget_zero_no_profile_field_falls_through(self) -> None:
        """Calls with profile=None still go through the adaptive path."""
        cfg = ThinkingConfig(mode="adaptive", budget_tokens=10000)
        body = build_thinking_extra_body(
            cfg,
            is_local=True,
            provider_supports_reasoning=True,
            max_output_tokens=8192,
            profile=None,
        )
        assert body is not None
        # Same legacy capping behavior
        assert body["chat_template_kwargs"]["thinking_budget"] == 4096


class TestProfileBudgetQualityKnobs:
    """Profile budget interacts correctly with reasoning_effort + small-model."""

    def test_reasoning_effort_low_scales_profile_budget_down(self) -> None:
        """reasoning_effort scaling still applies on top of profile budget."""
        cfg = ThinkingConfig(mode="enabled", budget_tokens=10000)
        profile = ModelProfile(
            name="profile-low-effort",
            supports_reasoning=True,
            is_local=True,
            default_thinking_budget=16384,
            reasoning_effort="low",  # 0.25× scale
            max_output_tokens=8192,
        )
        body = build_thinking_extra_body(
            cfg,
            is_local=True,
            provider_supports_reasoning=True,
            max_output_tokens=8192,
            profile=profile,
        )
        # 16384 * 0.25 = 4096
        assert body["chat_template_kwargs"]["thinking_budget"] == 4096

    def test_small_model_cap_clamps_profile_budget(self) -> None:
        """Small-model cap (4096) still clamps profile budget."""
        cfg = ThinkingConfig(mode="enabled", budget_tokens=10000)
        profile = ModelProfile(
            name="profile-small",
            supports_reasoning=True,
            is_local=True,
            default_thinking_budget=16384,
            is_small_model=True,
            max_output_tokens=8192,
        )
        body = build_thinking_extra_body(
            cfg,
            is_local=True,
            provider_supports_reasoning=True,
            max_output_tokens=8192,
            profile=profile,
        )
        # is_small_model caps at 4096
        assert body["chat_template_kwargs"]["thinking_budget"] == 4096


class TestProfileBudgetAnthropicFormat:
    """Profile budget routing also works through the anthropic-native wrapper."""

    def test_anthropic_native_format_with_profile_budget(self) -> None:
        cfg = ThinkingConfig(mode="enabled", budget_tokens=8000)
        profile = ModelProfile(
            name="claude-with-budget",
            provider_type="anthropic",
            supports_reasoning=True,
            is_local=False,
            thinking_extra_body_format="anthropic_native",
            default_thinking_budget=12000,
            max_output_tokens=16384,
        )
        body = build_thinking_extra_body(
            cfg,
            is_local=False,
            provider_supports_reasoning=True,
            max_output_tokens=16384,
            profile=profile,
        )
        assert body == {"thinking": {"type": "enabled", "budget_tokens": 12000}}


class TestProfileBudgetDoesNotChangeAdaptiveCloud:
    """Adaptive + cloud still returns None — no behavior shift on cloud paths."""

    def test_adaptive_cloud_returns_none_even_with_profile_budget(self) -> None:
        cfg = ThinkingConfig(mode="adaptive", budget_tokens=10000)
        profile = ModelProfile(
            name="cloud-profile",
            provider_type="openai-compat",
            is_local=False,
            default_thinking_budget=16384,
        )
        body = build_thinking_extra_body(
            cfg,
            is_local=False,
            provider_supports_reasoning=True,
            max_output_tokens=8192,
            profile=profile,
        )
        # adaptive + cloud unchanged from v2.6.0: cloud decides
        assert body is None

    def test_adaptive_local_no_reasoning_returns_disabled(self) -> None:
        """Profile budget never flips a no-reasoning model into thinking mode."""
        cfg = ThinkingConfig(mode="adaptive", budget_tokens=10000)
        profile = ModelProfile(
            name="local-no-reasoning",
            provider_type="openai-compat",
            supports_reasoning=False,
            is_local=True,
            default_thinking_budget=16384,
        )
        body = build_thinking_extra_body(
            cfg,
            is_local=True,
            provider_supports_reasoning=False,
            max_output_tokens=8192,
            profile=profile,
        )
        assert body == {"chat_template_kwargs": {"enable_thinking": False}}


class TestProfileBudgetThinkingBoost:
    """Boost flag still scales the profile budget — capped at profile ceiling."""

    def test_boost_doubles_profile_budget_clamped_to_profile_ceiling(self) -> None:
        cfg = ThinkingConfig(mode="enabled", budget_tokens=10000)
        profile = _glm_profile()
        runtime = SimpleNamespace(_thinking_boost_active=True)
        body = build_thinking_extra_body(
            cfg,
            is_local=True,
            provider_supports_reasoning=True,
            runtime=runtime,
            max_output_tokens=8192,
            profile=profile,
        )
        # 16384 * 2 = 32768, but max_budget=profile_budget (16384), so clamped to 16384
        # _apply_profile_budget_adjustments scales by reasoning_effort=high (1.0×)
        assert body["chat_template_kwargs"]["thinking_budget"] == 16384
        # Boost flag was consumed
        assert runtime._thinking_boost_active is False
