"""Tests for v14 Mechanism B — reasoning-content history filter.

The filter is a defensive pass that drops ``reasoning_content`` and
``reasoning`` keys from outbound assistant message dicts when the
active profile sets ``strip_prior_reasoning=True``. Today's stock
``_convert_message`` never writes these keys (inbound
``reasoning_content`` is converted to :class:`ThinkingBlock` and
ThinkingBlocks are dropped on the way out), so the filter is a
no-op in stock code. The tests below exercise the filter helper
directly with synthetic dicts AND drive ``_build_messages`` through
the whole pipeline to verify forward-compat protection.

Profiles that opt in (GLM-5.1 in the v14 GA; optionally
DeepSeek-R1) get an explicit guarantee that any future change which
lands a raw ``reasoning_content`` string on an outbound message will
have it stripped here.
"""
from __future__ import annotations

import logging
from dataclasses import replace

import pytest

import llm_code.api.openai_compat as openai_compat_module
from llm_code.api.openai_compat import (
    OpenAICompatProvider,
    _strip_reasoning_keys,
)
from llm_code.api.types import (
    Message,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
)
from llm_code.runtime.model_profile import ModelProfile


# =============================================================================
# _strip_reasoning_keys helper unit tests
# =============================================================================


class TestStripReasoningKeysHelper:
    def test_drops_reasoning_content_string(self) -> None:
        out = {"role": "assistant", "content": "hi", "reasoning_content": "hidden"}
        removed = _strip_reasoning_keys(out)
        assert "reasoning_content" not in out
        assert removed == len("hidden")

    def test_drops_reasoning_string(self) -> None:
        out = {"role": "assistant", "content": "hi", "reasoning": "hidden text"}
        removed = _strip_reasoning_keys(out)
        assert "reasoning" not in out
        assert removed == len("hidden text")

    def test_drops_both_keys_simultaneously(self) -> None:
        out = {
            "role": "assistant",
            "content": "hi",
            "reasoning_content": "AAA",
            "reasoning": "BB",
        }
        removed = _strip_reasoning_keys(out)
        assert "reasoning_content" not in out
        assert "reasoning" not in out
        assert removed == len("AAA") + len("BB")

    def test_preserves_other_keys(self) -> None:
        out = {
            "role": "assistant",
            "content": "hi",
            "tool_calls": [{"id": "x"}],
            "reasoning_content": "drop",
        }
        _strip_reasoning_keys(out)
        assert out["role"] == "assistant"
        assert out["content"] == "hi"
        assert out["tool_calls"] == [{"id": "x"}]

    def test_zero_removed_when_no_keys_present(self) -> None:
        out = {"role": "assistant", "content": "hi"}
        removed = _strip_reasoning_keys(out)
        assert removed == 0
        assert out == {"role": "assistant", "content": "hi"}

    def test_handles_non_string_value_via_repr(self) -> None:
        """Defensive — if a future change lands a non-string under
        ``reasoning_content`` (e.g. a list of structured blocks), the
        helper still removes it and counts repr bytes for the metric."""
        out = {
            "role": "assistant",
            "content": "hi",
            "reasoning_content": ["block1", "block2"],
        }
        removed = _strip_reasoning_keys(out)
        assert "reasoning_content" not in out
        assert removed > 0


# =============================================================================
# _build_messages integration tests with the filter
# =============================================================================


@pytest.fixture(autouse=True)
def _reset_warn_once() -> None:
    """Some tests build providers; reset the thinking-drop warn-once
    flag so an unrelated log doesn't pollute caplog."""
    openai_compat_module._thinking_drop_warned = False


def _make_provider_with_profile(
    *,
    strip_prior_reasoning: bool = False,
) -> OpenAICompatProvider:
    provider = OpenAICompatProvider(base_url="http://localhost:0", api_key="")
    # Override the resolved profile so the test scenario is independent
    # of which model id the harness happened to map to. Use ``replace``
    # to keep all other fields at their defaults.
    provider._profile = replace(
        provider._profile, strip_prior_reasoning=strip_prior_reasoning,
    )
    return provider


class TestBuildMessagesFilterFlagOff:
    def test_no_filter_when_flag_off(self) -> None:
        """Default profile (flag False) — _build_messages never strips
        reasoning keys. In stock code reasoning_content never reaches
        the outbound dict anyway, so the assertion is on the absence
        of strip-side logging."""
        provider = _make_provider_with_profile(strip_prior_reasoning=False)
        msg = Message(
            role="assistant",
            content=(TextBlock(text="hello"),),
        )
        result = provider._build_messages((msg,))
        assert result == [{"role": "assistant", "content": "hello"}]

    def test_thinking_drop_unaffected_when_flag_off(self) -> None:
        """Existing ThinkingBlock-drop behaviour (Wave2-1a P4) is
        preserved regardless of the flag."""
        provider = _make_provider_with_profile(strip_prior_reasoning=False)
        msg = Message(
            role="assistant",
            content=(
                ThinkingBlock(content="hidden"),
                TextBlock(text="visible"),
            ),
        )
        result = provider._build_messages((msg,))
        # Multi-block path: parts is a list; visible text appears,
        # thinking is dropped.
        assert result[0]["role"] == "assistant"
        parts = result[0]["content"]
        assert isinstance(parts, list)
        text_parts = [p["text"] for p in parts if p.get("type") == "text"]
        assert "visible" in text_parts


class TestBuildMessagesFilterFlagOn:
    def test_filter_passes_through_clean_dict_unchanged(self) -> None:
        """Stock code never adds reasoning_content to outbound dicts.
        With the flag ON, the filter still runs but finds nothing to
        strip — output is byte-identical to the flag-off path."""
        provider = _make_provider_with_profile(strip_prior_reasoning=True)
        msg = Message(
            role="assistant",
            content=(TextBlock(text="hello"),),
        )
        result = provider._build_messages((msg,))
        assert result == [{"role": "assistant", "content": "hello"}]

    def test_filter_drops_synthetic_reasoning_content(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Forward-compat — patch ``_convert_message`` to inject a
        synthetic ``reasoning_content`` key (simulating a hypothetical
        future change), then verify the filter strips it on the way
        out."""
        provider = _make_provider_with_profile(strip_prior_reasoning=True)
        original = provider._convert_message

        def _spiked(msg):
            out = original(msg)
            if out.get("role") == "assistant":
                out["reasoning_content"] = "leaked reasoning text"
            return out

        monkeypatch.setattr(provider, "_convert_message", _spiked)

        msg = Message(
            role="assistant",
            content=(TextBlock(text="hello"),),
        )
        result = provider._build_messages((msg,))
        # The filter ran on the spiked dict and removed the key.
        assert "reasoning_content" not in result[0]
        assert result[0]["content"] == "hello"

    def test_filter_drops_synthetic_reasoning_field(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """OpenAI o-series uses ``reasoning`` (not ``reasoning_content``).
        Both must be stripped under the same flag."""
        provider = _make_provider_with_profile(strip_prior_reasoning=True)
        original = provider._convert_message

        def _spiked(msg):
            out = original(msg)
            if out.get("role") == "assistant":
                out["reasoning"] = "o-series chain of thought"
            return out

        monkeypatch.setattr(provider, "_convert_message", _spiked)

        msg = Message(
            role="assistant",
            content=(TextBlock(text="hello"),),
        )
        result = provider._build_messages((msg,))
        assert "reasoning" not in result[0]

    def test_filter_skips_user_role(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Only ``role=assistant`` messages trigger the filter — user
        messages with synthetic reasoning fields (which would never
        happen organically but might leak through a bug) survive."""
        provider = _make_provider_with_profile(strip_prior_reasoning=True)
        original = provider._convert_message

        def _spiked(msg):
            out = original(msg)
            if out.get("role") == "user":
                out["reasoning_content"] = "user fake reasoning"
            return out

        monkeypatch.setattr(provider, "_convert_message", _spiked)

        msg = Message(role="user", content=(TextBlock(text="hi"),))
        result = provider._build_messages((msg,))
        # Filter is gated to assistant role; user dicts pass through.
        assert result[0].get("reasoning_content") == "user fake reasoning"

    def test_filter_skips_tool_role(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Tool result messages must pass through cleanly so the
        ``tool_call_id`` and ``content`` survive even with the filter
        enabled."""
        provider = _make_provider_with_profile(strip_prior_reasoning=True)
        msg = Message(
            role="user",
            content=(
                ToolResultBlock(tool_use_id="abc", content="file data"),
            ),
        )
        result = provider._build_messages((msg,))
        assert result[0]["role"] == "tool"
        assert result[0]["tool_call_id"] == "abc"
        assert result[0]["content"] == "file data"

    def test_multi_turn_history_strips_all_assistant_turns(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Spec §3.3 — across a 5-turn history with reasoning on each
        assistant turn, every assistant turn loses its reasoning."""
        provider = _make_provider_with_profile(strip_prior_reasoning=True)
        original = provider._convert_message

        def _spiked(msg):
            out = original(msg)
            if out.get("role") == "assistant":
                out["reasoning_content"] = f"turn-{out.get('content', '')}"
            return out

        monkeypatch.setattr(provider, "_convert_message", _spiked)

        history = (
            Message(role="user", content=(TextBlock(text="q1"),)),
            Message(role="assistant", content=(TextBlock(text="a1"),)),
            Message(role="user", content=(TextBlock(text="q2"),)),
            Message(role="assistant", content=(TextBlock(text="a2"),)),
            Message(role="user", content=(TextBlock(text="q3"),)),
            Message(role="assistant", content=(TextBlock(text="a3"),)),
        )
        result = provider._build_messages(history)
        assistant_dicts = [r for r in result if r.get("role") == "assistant"]
        assert len(assistant_dicts) == 3
        for d in assistant_dicts:
            assert "reasoning_content" not in d


class TestBuildMessagesFilterLogging:
    def test_emits_one_log_line_per_provider_call(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Spec §5 — aggregate one INFO line per provider call (not one
        per message). Three assistant turns with reasoning leaked
        ⇒ exactly one log line summarising turns + total bytes."""
        provider = _make_provider_with_profile(strip_prior_reasoning=True)
        original = provider._convert_message

        def _spiked(msg):
            out = original(msg)
            if out.get("role") == "assistant":
                out["reasoning_content"] = "AAA"  # 3 bytes per turn
            return out

        monkeypatch.setattr(provider, "_convert_message", _spiked)

        history = (
            Message(role="assistant", content=(TextBlock(text="a1"),)),
            Message(role="assistant", content=(TextBlock(text="a2"),)),
            Message(role="assistant", content=(TextBlock(text="a3"),)),
        )
        with caplog.at_level(
            logging.INFO, logger="llm_code.api.openai_compat",
        ):
            provider._build_messages(history)
        strip_logs = [
            r for r in caplog.records
            if "tool_consumption: reasoning_stripped" in r.message
        ]
        assert len(strip_logs) == 1
        # Format: "...turns=3 total_bytes=9"
        line = strip_logs[0].message
        assert "turns=3" in line
        assert "total_bytes=9" in line

    def test_no_log_when_nothing_stripped(
        self, caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Filter ON but no reasoning keys present → no log emitted."""
        provider = _make_provider_with_profile(strip_prior_reasoning=True)
        history = (
            Message(role="assistant", content=(TextBlock(text="a1"),)),
        )
        with caplog.at_level(
            logging.INFO, logger="llm_code.api.openai_compat",
        ):
            provider._build_messages(history)
        strip_logs = [
            r for r in caplog.records
            if "tool_consumption: reasoning_stripped" in r.message
        ]
        assert len(strip_logs) == 0

    def test_no_log_when_flag_off(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Even with synthetic reasoning_content present, flag OFF
        means no filter, no log."""
        provider = _make_provider_with_profile(strip_prior_reasoning=False)
        original = provider._convert_message

        def _spiked(msg):
            out = original(msg)
            if out.get("role") == "assistant":
                out["reasoning_content"] = "leaked"
            return out

        monkeypatch.setattr(provider, "_convert_message", _spiked)

        history = (
            Message(role="assistant", content=(TextBlock(text="a1"),)),
        )
        with caplog.at_level(
            logging.INFO, logger="llm_code.api.openai_compat",
        ):
            result = provider._build_messages(history)
        strip_logs = [
            r for r in caplog.records
            if "tool_consumption: reasoning_stripped" in r.message
        ]
        assert len(strip_logs) == 0
        # And the synthetic key survives — that's the byte-parity
        # promise for opt-out profiles.
        assert result[0]["reasoning_content"] == "leaked"


class TestProfileOptInGlm:
    def test_default_profile_does_not_opt_in(self) -> None:
        """Default ModelProfile has the flag OFF — only profiles that
        explicitly opt in (GLM-5.1, optionally DeepSeek-R1) get the
        forward-compat filter."""
        profile = ModelProfile()
        assert profile.strip_prior_reasoning is False

    def test_glm_profile_toml_opts_in(self) -> None:
        """The packaged 65-glm-5.1.toml under examples/model_profiles/
        opts in to ``strip_prior_reasoning``. End users copy this file
        to ~/.llmcode/model_profiles/ and the flag activates."""
        from pathlib import Path
        import sys
        if sys.version_info >= (3, 11):
            import tomllib
        else:
            import tomli as tomllib  # type: ignore[import-not-found]
        repo_root = Path(__file__).resolve().parents[2]
        glm_toml = (
            repo_root / "examples" / "model_profiles" / "65-glm-5.1.toml"
        )
        with open(glm_toml, "rb") as f:
            data = tomllib.load(f)
        section = data.get("tool_consumption", {})
        assert section.get("strip_prior_reasoning") is True
