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

        v2.9.2: GLM-style profiles (``reasoning_field =
        "reasoning_content"``) get clamped to 512 floor when compile
        budget is below 512; setting 0 no longer fully disables on
        these profiles. See ``TestV292ReasoningContentRuntimeFloor``
        for the safeguard tests.
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
        # v2.9.2 — clamped to 512 floor (GLM reasoning_content profile).
        assert body["chat_template_kwargs"]["enable_thinking"] is True
        assert body["chat_template_kwargs"]["thinking_budget"] == 512

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

    def test_zero_compile_budget_clamped_for_reasoning_content(self) -> None:
        """v2.9.2: GLM-style profiles get a 512 floor.

        The original v2.9.0 semantic — "compile_budget=0 fully
        disables thinking" — is preserved for non-reasoning_content
        profiles via ``test_anthropic_disabled_block`` below.
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
            tool_calls_this_turn=3,
        )
        assert body is not None
        # v2.9.2 clamp — 0 → 512 for reasoning_content profiles.
        ctk = body["chat_template_kwargs"]
        assert ctk == {"enable_thinking": True, "thinking_budget": 512}

    def test_nonzero_compile_budget_above_floor_unchanged(self) -> None:
        """If a profile sets ``compile_thinking_budget = 768`` (above
        the 512 floor, below the 1024 post-tool budget), the lever
        engages and the budget passes through verbatim.
        """
        cfg = ThinkingConfig(mode="enabled", budget_tokens=10000)
        profile = _glm_profile_with_compile_lever(
            compile_after=3, compile_budget=768,
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
        assert body["chat_template_kwargs"]["thinking_budget"] == 768


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
        post_tool_thinking_budget is ignored even though it's set.

        v2.9.2: on GLM-style profiles, compile_budget=0 is clamped
        to 512, but the supersede semantic still holds — 512 is
        still ≠ post_tool_budget=1024, proving compile won.
        """
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
        # 512 (clamped compile floor), NOT 1024 (post-tool) and NOT 16384 (default).
        assert body["chat_template_kwargs"] == {
            "enable_thinking": True,
            "thinking_budget": 512,
        }


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


# ── v2.9.1 hotfix regression ─────────────────────────────────────────


class TestV291CompileBudgetFloor:
    """v2.9.1 hotfix — GLM-5.1's ``reasoning_content`` channel needs a
    non-zero floor on the compile step.

    A real smoke test on ``查詢今日熱門新聞三則`` with
    ``compile_thinking_budget = 0`` tripped llmcode's ``empty response
    fallback``: the model emitted a tool-call wrapper but no visible
    text because llama.cpp had nothing to route through the reasoning
    channel. v2.9.1 pins the GLM profile floor at 512.

    This regression test loads the shipped
    ``examples/model_profiles/65-glm-5.1.toml`` directly so a future
    PR that flips it back to 0 fails CI loudly.
    """

    def test_shipped_glm_profile_has_nonzero_compile_floor(self) -> None:
        from pathlib import Path

        try:
            import tomllib
        except ImportError:  # Python 3.10
            import tomli as tomllib  # type: ignore[import-not-found, no-redef]

        profile_path = (
            Path(__file__).resolve().parents[2]
            / "examples"
            / "model_profiles"
            / "65-glm-5.1.toml"
        )
        with profile_path.open("rb") as fh:
            data = tomllib.load(fh)

        budget = data["tool_consumption"]["compile_thinking_budget"]
        assert budget >= 512, (
            f"GLM-5.1 reasoning_content channel needs >= 512 floor; "
            f"got {budget}. Setting 0 caused empty-response fallback "
            f"in real smoke test (v2.9.1 hotfix)."
        )

    def test_glm_compile_threshold_still_triggers_at_3(self) -> None:
        """The hotfix changes the budget value, NOT the trigger gate.

        ``compile_after_tool_calls = 3`` should be unchanged so the
        wall-clock-win threshold still fires at the right point.
        """
        from pathlib import Path

        try:
            import tomllib
        except ImportError:  # Python 3.10
            import tomli as tomllib  # type: ignore[import-not-found, no-redef]

        profile_path = (
            Path(__file__).resolve().parents[2]
            / "examples"
            / "model_profiles"
            / "65-glm-5.1.toml"
        )
        with profile_path.open("rb") as fh:
            data = tomllib.load(fh)

        assert data["tool_consumption"]["compile_after_tool_calls"] == 3


# ── v2.9.2 hotfix — runtime safeguard ──────────────────────────────


class TestV292ReasoningContentRuntimeFloor:
    """v2.9.2 — runtime clamp for ``reasoning_content`` profiles.

    The v2.9.1 fix only edited the *example* profile under
    ``examples/model_profiles/``, which is **not packaged into the
    wheel**. Users on v2.9.0 who already copied the example to
    ``~/.llmcode/model_profiles/glm-5.1.toml`` would still have
    ``compile_thinking_budget = 0`` after ``pip install -U
    llmcode-cli==2.9.1`` — runtime stays broken.

    v2.9.2 adds a defensive clamp inside ``build_thinking_extra_body``:
    when the profile uses a separate ``reasoning_content`` channel
    AND the resolved compile budget is below 512, force it to 512.
    Other models (Anthropic, OpenAI, etc.) still respect a 0 budget
    as "disable thinking entirely".
    """

    def _glm_profile(self, *, compile_budget: int) -> ModelProfile:
        """GLM-shaped profile with the given compile budget."""
        return ModelProfile(
            name="GLM-5.1",
            provider_type="openai-compat",
            native_tools=False,
            supports_reasoning=True,
            force_xml_tools=True,
            implicit_thinking=False,
            reasoning_field="reasoning_content",
            thinking_extra_body_format="chat_template_kwargs",
            default_thinking_budget=16384,
            post_tool_thinking_budget=1024,
            compile_after_tool_calls=3,
            compile_thinking_budget=compile_budget,
            default_temperature=0.6,
            is_local=True,
            max_output_tokens=8192,
        )

    def _generic_profile(self, *, compile_budget: int) -> ModelProfile:
        """Profile WITHOUT reasoning_content — Anthropic-style."""
        return ModelProfile(
            name="Generic",
            provider_type="anthropic",
            native_tools=True,
            supports_reasoning=True,
            reasoning_field="",  # default — single channel
            default_thinking_budget=16384,
            post_tool_thinking_budget=1024,
            compile_after_tool_calls=3,
            compile_thinking_budget=compile_budget,
            max_output_tokens=8192,
        )

    def _build(self, profile: ModelProfile) -> dict | None:
        cfg = ThinkingConfig(mode="enabled", budget_tokens=8192)
        return build_thinking_extra_body(
            cfg,
            is_local=True,
            provider_supports_reasoning=True,
            max_output_tokens=8192,
            profile=profile,
            post_tool_iteration=True,
            tool_calls_this_turn=3,
        )

    def test_zero_budget_clamped_to_512_for_reasoning_content(self) -> None:
        """compile_budget=0 + reasoning_content → clamped to 512."""
        body = self._build(self._glm_profile(compile_budget=0))
        assert body is not None, "thinking should NOT be fully disabled"
        assert body["chat_template_kwargs"]["thinking_budget"] == 512

    def test_partial_budget_clamped_to_512(self) -> None:
        """compile_budget=256 + reasoning_content → clamped to 512."""
        body = self._build(self._glm_profile(compile_budget=256))
        assert body is not None
        assert body["chat_template_kwargs"]["thinking_budget"] == 512

    def test_512_budget_unchanged(self) -> None:
        """compile_budget=512 → not clamped (already at floor)."""
        body = self._build(self._glm_profile(compile_budget=512))
        assert body is not None
        assert body["chat_template_kwargs"]["thinking_budget"] == 512

    def test_high_budget_not_clamped_down(self) -> None:
        """compile_budget=2048 → preserved (clamp is a floor, not a cap)."""
        body = self._build(self._glm_profile(compile_budget=2048))
        assert body is not None
        assert body["chat_template_kwargs"]["thinking_budget"] == 2048

    def test_non_reasoning_content_profile_still_disables_at_zero(self) -> None:
        """Anthropic-style (single channel) → 0 still disables."""
        body = self._build(self._generic_profile(compile_budget=0))
        # The wrap shape varies by provider, but disabled thinking
        # should produce ``None`` or a ``"disabled"`` shape — never
        # a 512 budget.
        if body is not None:
            budget = body.get("chat_template_kwargs", {}).get("thinking_budget", 0)
            assert budget == 0, (
                "non-reasoning_content profiles must still respect "
                "compile_budget=0 as 'disable thinking entirely'"
            )
