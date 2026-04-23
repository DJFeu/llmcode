"""M6 Task 6.2 — canonical span attribute schema tests.

Verifies every documented attribute constant lives on the module, is in
``ALLOWED_ATTRIBUTE_KEYS``, and that ``set_attr_safe`` refuses attributes
outside the allow-list. Also pins the ``args_hash`` helper contract.
"""
from __future__ import annotations

import pytest


class TestAttributeConstants:
    def test_pipeline_constants_exist(self) -> None:
        from llm_code.engine.observability import attributes as attrs

        assert attrs.PIPELINE_NAME == "llmcode.pipeline.name"
        assert attrs.COMPONENT_NAME == "llmcode.component.name"
        assert attrs.COMPONENT_INPUT_KEYS == "llmcode.component.input_keys"
        assert attrs.COMPONENT_OUTPUT_KEYS == "llmcode.component.output_keys"

    def test_agent_constants_exist(self) -> None:
        from llm_code.engine.observability import attributes as attrs

        assert attrs.AGENT_ITERATION == "llmcode.agent.iteration"
        assert attrs.AGENT_MODE == "llmcode.agent.mode"
        assert attrs.AGENT_EXIT_REASON == "llmcode.agent.exit_reason"
        assert attrs.AGENT_DEGRADED == "llmcode.agent.degraded"

    def test_tool_constants_exist(self) -> None:
        from llm_code.engine.observability import attributes as attrs

        assert attrs.TOOL_NAME == "llmcode.tool.name"
        assert attrs.TOOL_ARGS_HASH == "llmcode.tool.args_hash"
        assert attrs.TOOL_RESULT_IS_ERROR == "llmcode.tool.result_is_error"
        assert attrs.TOOL_RETRY_ATTEMPT == "llmcode.tool.retry_attempt"
        assert attrs.TOOL_FALLBACK_FROM == "llmcode.tool.fallback_from"

    def test_model_constants_follow_gen_ai_namespace(self) -> None:
        from llm_code.engine.observability import attributes as attrs

        assert attrs.MODEL_NAME == "gen_ai.request.model"
        assert attrs.MODEL_PROVIDER == "gen_ai.system"
        assert attrs.TOKENS_IN == "gen_ai.usage.prompt_tokens"
        assert attrs.TOKENS_OUT == "gen_ai.usage.completion_tokens"
        assert attrs.MODEL_TEMPERATURE == "gen_ai.request.temperature"

    def test_session_constants_exist(self) -> None:
        from llm_code.engine.observability import attributes as attrs

        assert attrs.SESSION_ID == "llmcode.session.id"
        assert attrs.USER_PROMPT_LENGTH == "llmcode.user_prompt.length"


class TestAllowListCoverage:
    def test_allowed_attribute_keys_is_frozenset(self) -> None:
        from llm_code.engine.observability.attributes import ALLOWED_ATTRIBUTE_KEYS

        assert isinstance(ALLOWED_ATTRIBUTE_KEYS, frozenset)

    def test_every_documented_constant_in_allow_list(self) -> None:
        """All `*_NAME`-style constants defined on the module live in the
        allow-list — otherwise ``set_attr_safe`` can't be called with them."""
        from llm_code.engine.observability import attributes as attrs

        documented = [
            attrs.PIPELINE_NAME, attrs.COMPONENT_NAME,
            attrs.COMPONENT_INPUT_KEYS, attrs.COMPONENT_OUTPUT_KEYS,
            attrs.AGENT_ITERATION, attrs.AGENT_MODE,
            attrs.AGENT_EXIT_REASON, attrs.AGENT_DEGRADED,
            attrs.TOOL_NAME, attrs.TOOL_ARGS_HASH,
            attrs.TOOL_RESULT_IS_ERROR, attrs.TOOL_RETRY_ATTEMPT,
            attrs.TOOL_FALLBACK_FROM, attrs.MODEL_NAME,
            attrs.MODEL_PROVIDER, attrs.TOKENS_IN,
            attrs.TOKENS_OUT, attrs.MODEL_TEMPERATURE,
            attrs.SESSION_ID, attrs.USER_PROMPT_LENGTH,
        ]
        for key in documented:
            assert key in attrs.ALLOWED_ATTRIBUTE_KEYS, (
                f"{key!r} missing from ALLOWED_ATTRIBUTE_KEYS"
            )


class TestSetAttrSafeGuard:
    def test_set_attr_safe_accepts_allowed_key(self) -> None:
        from llm_code.engine.observability.attributes import (
            TOOL_NAME,
            set_attr_safe,
        )

        captured: dict[str, object] = {}

        class FakeSpan:
            def set_attribute(self, key: str, value: object) -> None:
                captured[key] = value

        set_attr_safe(FakeSpan(), TOOL_NAME, "bash")
        assert captured == {TOOL_NAME: "bash"}

    def test_set_attr_safe_rejects_unknown_key(self) -> None:
        from llm_code.engine.observability.attributes import set_attr_safe

        class FakeSpan:
            def set_attribute(self, key: str, value: object) -> None:  # noqa: ARG002
                pytest.fail("set_attribute should not be called for rejected key")

        with pytest.raises(ValueError):
            set_attr_safe(FakeSpan(), "not.in.allow_list", "whatever")

    def test_set_attr_safe_noop_on_none_span(self) -> None:
        """Passing ``None`` as span (OTel missing) silently no-ops."""
        from llm_code.engine.observability.attributes import (
            TOOL_NAME,
            set_attr_safe,
        )

        # Should not raise.
        set_attr_safe(None, TOOL_NAME, "bash")


class TestArgsHash:
    def test_args_hash_deterministic(self) -> None:
        from llm_code.engine.observability.attributes import args_hash

        a = args_hash({"cmd": "ls", "cwd": "/tmp"})
        b = args_hash({"cwd": "/tmp", "cmd": "ls"})
        assert a == b

    def test_args_hash_short_hex_digest(self) -> None:
        from llm_code.engine.observability.attributes import args_hash

        h = args_hash({"x": 1})
        assert isinstance(h, str)
        assert len(h) == 16
        int(h, 16)  # must be a valid hex string

    def test_args_hash_different_inputs_differ(self) -> None:
        from llm_code.engine.observability.attributes import args_hash

        assert args_hash({"x": 1}) != args_hash({"x": 2})
