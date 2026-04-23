"""End-to-end tests for the OpenAI-compatible endpoint.

These tests drive the FastAPI app via ``TestClient`` and assert the
wire-format matches what an OpenAI client (``openai`` SDK, LangChain,
litellm) expects.

Runs only when the ``hayhooks`` extras are installed.
"""
from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sse_starlette")

from fastapi.testclient import TestClient

from llm_code.hayhooks.openai_compat import build_app
from llm_code.hayhooks.session import HayhooksSession


@pytest.fixture
def app(hayhooks_config, mock_agent, bearer_env):
    def _factory(config, fingerprint):
        return HayhooksSession(
            config=config, fingerprint=fingerprint, agent=mock_agent,
        )

    return build_app(hayhooks_config, session_factory=_factory)


@pytest.fixture
def client(app):
    return TestClient(app)


class TestOpenAIParity:
    def test_non_streaming_full_exchange(self, client, bearer_env, mock_agent):
        mock_agent.text = "the final answer"
        r = client.post(
            "/v1/chat/completions",
            json={
                "model": "llmcode-default",
                "messages": [{"role": "user", "content": "what is 2+2?"}],
            },
            headers={"Authorization": f"Bearer {bearer_env}"},
        )
        assert r.status_code == 200
        body = r.json()
        # OpenAI-compatible shape
        assert body["object"] == "chat.completion"
        assert body["model"] == "llmcode-default"
        assert len(body["choices"]) == 1
        assert body["choices"][0]["message"] == {
            "role": "assistant",
            "content": "the final answer",
        }
        assert body["choices"][0]["finish_reason"] == "stop"
        assert "id" in body
        assert body["id"].startswith("chatcmpl-")
        usage = body["usage"]
        assert usage["total_tokens"] == usage["prompt_tokens"] + usage["completion_tokens"]

    def test_streaming_full_exchange(self, client, bearer_env, mock_agent):
        mock_agent.stream_events = [
            {"type": "text_delta", "text": "hello"},
            {"type": "text_delta", "text": " world"},
        ]
        r = client.post(
            "/v1/chat/completions",
            json={
                "model": "llmcode-default",
                "messages": [{"role": "user", "content": "say hi"}],
                "stream": True,
            },
            headers={"Authorization": f"Bearer {bearer_env}"},
        )
        assert r.status_code == 200
        assert "text/event-stream" in r.headers["content-type"]
        # sse-starlette prefixes each event with "data: "; strip and parse.
        chunks = []
        for raw in r.text.splitlines():
            if not raw.startswith("data:"):
                continue
            payload = raw[len("data:"):].strip()
            if payload == "[DONE]":
                chunks.append("[DONE]")
                continue
            try:
                chunks.append(json.loads(payload))
            except json.JSONDecodeError:
                continue
        assert chunks[-1] == "[DONE]"
        # First chunk should announce the assistant role.
        assert chunks[0]["choices"][0]["delta"].get("role") == "assistant"
        # Final non-[DONE] chunk carries a finish_reason.
        last_real = [c for c in chunks if c != "[DONE]"][-1]
        assert last_real["choices"][0]["finish_reason"] == "stop"

    def test_model_envelope_shape(self, client, bearer_env):
        r = client.get(
            "/v1/models",
            headers={"Authorization": f"Bearer {bearer_env}"},
        )
        assert r.status_code == 200
        body = r.json()
        for entry in body["data"]:
            assert {"id", "object", "owned_by"} <= set(entry.keys())

    def test_pen_test_401_without_auth(self, client):
        r = client.post(
            "/v1/chat/completions",
            json={"model": "m", "messages": [{"role": "user", "content": "x"}]},
        )
        assert r.status_code == 401
        body = r.json()
        assert body["error"]["type"] == "authentication_error"

    def test_pen_test_401_with_wrong_token(self, client):
        r = client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer totally-wrong"},
            json={"model": "m", "messages": [{"role": "user", "content": "x"}]},
        )
        assert r.status_code == 401

    def test_pen_test_413_on_oversized_payload(self, client, bearer_env):
        huge = "z" * (1_000_001)
        r = client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {bearer_env}"},
            json={
                "model": "m",
                "messages": [{"role": "user", "content": huge}],
            },
        )
        assert r.status_code == 413
