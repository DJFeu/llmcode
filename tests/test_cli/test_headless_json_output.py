"""Tests for v16 M8 headless mode + JSON output schema.

Covers:

* ``run_quick_mode(output_format='json')`` emits a single JSON object
  to stdout matching ``tests/fixtures/headless_output.schema.json``.
* Exit codes: 0=success, 1=tool error, 2=model error, 3=auth error,
  4=user cancel.
* CLI flag wiring: ``--headless`` implies JSON output + structured
  exit codes; ``-q "..."`` keeps text output as before.
* ``-q`` backward compatibility — existing invocations are unchanged.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from llm_code.cli.oneshot import run_quick_mode
from llm_code.runtime.config import RuntimeConfig


SCHEMA_PATH = Path(__file__).parent.parent / "fixtures" / "headless_output.schema.json"


# ---------------------------------------------------------------------------
# Schema fixture
# ---------------------------------------------------------------------------


def _load_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _validate_schema(payload: dict) -> None:
    """Lightweight validator — no jsonschema dep, just shape checks."""
    schema = _load_schema()
    required = schema["required"]
    for field in required:
        assert field in payload, f"missing required field {field}"
    assert isinstance(payload["output"], str)
    assert isinstance(payload["tool_calls"], list)
    assert isinstance(payload["tokens"], dict)
    assert "input" in payload["tokens"] and "output" in payload["tokens"]
    assert isinstance(payload["exit_code"], int)
    assert 0 <= payload["exit_code"] <= 4
    assert payload["error"] is None or isinstance(payload["error"], str)


# ---------------------------------------------------------------------------
# Schema test
# ---------------------------------------------------------------------------


class TestSchema:
    def test_schema_loads(self) -> None:
        schema = _load_schema()
        assert schema["$schema"]
        assert schema["title"] == "llmcode headless output"

    def test_schema_required_fields_present(self) -> None:
        schema = _load_schema()
        assert set(schema["required"]) == {
            "output", "tool_calls", "tokens", "exit_code", "error",
        }


# ---------------------------------------------------------------------------
# run_quick_mode JSON output
# ---------------------------------------------------------------------------


@pytest.fixture()
def stub_config() -> RuntimeConfig:
    return RuntimeConfig(
        model="stub-model",
        provider_api_key_env="LLM_API_KEY",
        provider_base_url="http://localhost:0",
    )


@pytest.fixture(autouse=True)
def _no_real_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stop ``run_quick_mode`` from contacting any real provider.

    ``_create_provider`` is patched to return a MagicMock; the runtime
    is patched to skip ``run_one_turn`` and return a synthetic event
    list so each test controls exactly which exit-code path is taken.
    """
    # Patch resolve_api_key so missing env vars don't blow up.
    from llm_code.runtime import auth

    monkeypatch.setattr(auth, "resolve_api_key", lambda env_var: "stub-key")
    # Block actual ProviderClient.from_model from running.
    from llm_code.api import client

    monkeypatch.setattr(
        client.ProviderClient,
        "from_model",
        staticmethod(lambda *a, **kw: MagicMock()),
    )


def _patch_runtime_with(events: list, monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch ConversationRuntime so run_one_turn returns ``events``."""
    from llm_code.runtime import conversation

    real_init = conversation.ConversationRuntime.__init__

    def fake_init(self, *args, **kwargs):
        real_init(self, *args, **kwargs)
        async def _run_one_turn(_text):
            return events
        self.run_one_turn = _run_one_turn  # type: ignore[assignment]
        self.shutdown = lambda: None

    monkeypatch.setattr(conversation.ConversationRuntime, "__init__", fake_init)


def _patch_runtime_raises(exc: Exception, monkeypatch: pytest.MonkeyPatch) -> None:
    from llm_code.runtime import conversation

    real_init = conversation.ConversationRuntime.__init__

    def fake_init(self, *args, **kwargs):
        real_init(self, *args, **kwargs)
        async def _run_one_turn(_text):
            raise exc
        self.run_one_turn = _run_one_turn  # type: ignore[assignment]
        self.shutdown = lambda: None

    monkeypatch.setattr(conversation.ConversationRuntime, "__init__", fake_init)


class TestQuickModeJson:
    def test_success_emits_json_with_exit_zero(
        self,
        stub_config: RuntimeConfig,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        from llm_code.api.types import StreamTextDelta

        events = [StreamTextDelta(text="Hello world")]
        _patch_runtime_with(events, monkeypatch)

        exit_code = run_quick_mode(
            "Say hi", stub_config,
            output_format="json", headless=True,
        )
        captured = capsys.readouterr()
        payload = json.loads(captured.out.strip())
        _validate_schema(payload)
        assert payload["output"] == "Hello world"
        assert payload["exit_code"] == 0
        assert payload["error"] is None
        assert exit_code == 0

    def test_success_filters_implicit_thinking_tags(
        self,
        stub_config: RuntimeConfig,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        from llm_code.api.types import StreamTextDelta

        events = [StreamTextDelta(text="hidden reasoning</think>visible")]
        _patch_runtime_with(events, monkeypatch)

        exit_code = run_quick_mode(
            "Say hi", stub_config,
            output_format="json", headless=True,
        )
        captured = capsys.readouterr()
        payload = json.loads(captured.out.strip())
        _validate_schema(payload)
        assert payload["output"] == "visible"
        assert exit_code == 0

    def test_provider_error_returns_exit_2(
        self,
        stub_config: RuntimeConfig,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        from llm_code.api.errors import ProviderError

        _patch_runtime_raises(ProviderError("upstream bork"), monkeypatch)

        exit_code = run_quick_mode(
            "Bork", stub_config,
            output_format="json", headless=True,
        )
        captured = capsys.readouterr()
        payload = json.loads(captured.out.strip())
        _validate_schema(payload)
        assert payload["exit_code"] == 2
        assert "Provider error" in payload["error"]
        assert exit_code == 2

    def test_auth_error_returns_exit_3(
        self,
        stub_config: RuntimeConfig,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        from llm_code.api.errors import ProviderAuthError

        _patch_runtime_raises(ProviderAuthError("bad key"), monkeypatch)

        exit_code = run_quick_mode(
            "anything", stub_config,
            output_format="json", headless=True,
        )
        captured = capsys.readouterr()
        payload = json.loads(captured.out.strip())
        _validate_schema(payload)
        assert payload["exit_code"] == 3
        assert "Auth error" in payload["error"]
        assert exit_code == 3

    def test_keyboard_interrupt_returns_exit_4(
        self,
        stub_config: RuntimeConfig,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        _patch_runtime_raises(KeyboardInterrupt(), monkeypatch)

        exit_code = run_quick_mode(
            "anything", stub_config,
            output_format="json", headless=True,
        )
        captured = capsys.readouterr()
        payload = json.loads(captured.out.strip())
        _validate_schema(payload)
        assert payload["exit_code"] == 4
        assert payload["error"] == "User cancel"
        assert exit_code == 4

    def test_generic_exception_returns_exit_1(
        self,
        stub_config: RuntimeConfig,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        _patch_runtime_raises(ValueError("tool blew up"), monkeypatch)

        exit_code = run_quick_mode(
            "anything", stub_config,
            output_format="json", headless=True,
        )
        captured = capsys.readouterr()
        payload = json.loads(captured.out.strip())
        _validate_schema(payload)
        assert payload["exit_code"] == 1
        assert "Tool error" in payload["error"]
        assert exit_code == 1


# ---------------------------------------------------------------------------
# Tool calls captured in headless output
# ---------------------------------------------------------------------------


class TestToolCallCapture:
    def test_tool_calls_in_payload(
        self,
        stub_config: RuntimeConfig,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        from llm_code.api.types import StreamTextDelta, StreamToolUseStart

        events = [
            StreamToolUseStart(id="t1", name="read_file"),
            StreamTextDelta(text="done"),
        ]
        _patch_runtime_with(events, monkeypatch)

        run_quick_mode(
            "Read foo.py", stub_config,
            output_format="json", headless=True,
        )
        captured = capsys.readouterr()
        payload = json.loads(captured.out.strip())
        assert len(payload["tool_calls"]) == 1
        assert payload["tool_calls"][0]["name"] == "read_file"

    def test_xml_tool_exec_starts_are_captured(
        self,
        stub_config: RuntimeConfig,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        from llm_code.api.types import StreamTextDelta, StreamToolExecStart

        events = [
            StreamToolExecStart(
                tool_name="web_search",
                args_summary="query='today news'",
                tool_id="xml-1",
            ),
            StreamTextDelta(text="done"),
        ]
        _patch_runtime_with(events, monkeypatch)

        run_quick_mode(
            "Search news", stub_config,
            output_format="json", headless=True,
        )
        captured = capsys.readouterr()
        payload = json.loads(captured.out.strip())
        assert payload["tool_calls"] == [
            {
                "name": "web_search",
                "id": "xml-1",
                "args_summary": "query='today news'",
            }
        ]


# ---------------------------------------------------------------------------
# Backward compat — text path unchanged
# ---------------------------------------------------------------------------


class TestTextBackwardCompat:
    def test_text_output_default(
        self,
        stub_config: RuntimeConfig,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        from llm_code.api.types import StreamTextDelta

        events = [StreamTextDelta(text="text-only output")]
        _patch_runtime_with(events, monkeypatch)
        # output_format defaults to "text", headless defaults to False.
        exit_code = run_quick_mode("anything", stub_config)
        captured = capsys.readouterr()
        # Text path prints the visible reply directly, no JSON wrapper.
        assert "text-only output" in captured.out
        assert "exit_code" not in captured.out
        assert exit_code == 0

    def test_text_path_does_not_raise_on_provider_error(
        self,
        stub_config: RuntimeConfig,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        # When NOT in headless mode, the function still returns 0 so
        # the ``-q "..."`` shell pipeline works as it always did.
        from llm_code.api.errors import ProviderError

        _patch_runtime_raises(ProviderError("oops"), monkeypatch)
        exit_code = run_quick_mode("anything", stub_config)
        assert exit_code == 0
