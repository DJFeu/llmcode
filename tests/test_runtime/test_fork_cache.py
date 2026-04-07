"""Tests for fork cache key derivation and MessageRequest cache_key field."""
from __future__ import annotations

from llm_code.api.types import Message, MessageRequest, TextBlock
from llm_code.runtime.fork_cache import derive_fork_key


def test_derive_fork_key_deterministic():
    assert derive_fork_key("sess-abc", "reviewer") == "sess-abc:fork:reviewer"
    assert derive_fork_key("sess-abc", "reviewer") == derive_fork_key("sess-abc", "reviewer")


def test_derive_fork_key_handles_empty():
    assert derive_fork_key("", "reviewer") == "root:fork:reviewer"
    assert derive_fork_key("sess", "") == "sess:fork:anon"


def test_message_request_cache_key_default_empty():
    req = MessageRequest(
        model="m", messages=(Message(role="user", content=(TextBlock(text="hi"),)),)
    )
    assert req.cache_key == ""
    assert req.metadata is None


def test_message_request_cache_key_set():
    req = MessageRequest(
        model="m",
        messages=(Message(role="user", content=(TextBlock(text="hi"),)),),
        cache_key="parent:fork:reviewer",
    )
    assert req.cache_key == "parent:fork:reviewer"


def test_orchestrate_executor_passes_cache_key():
    """inline_persona_executor should set cache_key from runtime+persona."""
    import asyncio
    from types import SimpleNamespace

    from llm_code.api.types import MessageResponse, TokenUsage
    from llm_code.runtime.orchestrate_executor import inline_persona_executor

    captured: dict = {}

    class FakeProvider:
        async def send_message(self, request):
            captured["req"] = request
            return MessageResponse(
                content=(TextBlock(text="ok"),),
                usage=TokenUsage(input_tokens=1, output_tokens=1),
                stop_reason="end",
            )

    runtime = SimpleNamespace(
        _provider=FakeProvider(),
        _config=SimpleNamespace(model="test-model"),
        session=SimpleNamespace(session_id="sess-xyz"),
    )
    persona = SimpleNamespace(
        name="planner", system_prompt="you plan", temperature=0.3
    )
    ok, text = asyncio.run(inline_persona_executor(runtime, persona, "do thing"))
    assert ok is True
    assert captured["req"].cache_key == "sess-xyz:fork:planner"
