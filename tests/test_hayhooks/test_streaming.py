"""Tests for the streaming adapter."""
from __future__ import annotations

import json


from llm_code.hayhooks.session import AgentResult
from llm_code.hayhooks.streaming import (
    AgentEvent,
    _final_chunk,
    _role_chunk,
    agent_events_to_openai_chunks,
    agent_events_to_sse_lines,
)


async def _aiter(seq):
    for e in seq:
        yield e


class TestOpenaiChunks:
    async def test_emits_role_then_content_then_final(self):
        events = _aiter([
            {"type": "text_delta", "text": "hello "},
            {"type": "text_delta", "text": "world"},
            {"type": "done", "result": AgentResult(exit_reason="stop")},
        ])
        chunks = [c async for c in agent_events_to_openai_chunks(events, "my-model")]
        # role -> 2 deltas -> final
        assert len(chunks) == 4
        assert chunks[0]["choices"][0]["delta"] == {"role": "assistant"}
        assert chunks[1]["choices"][0]["delta"]["content"] == "hello "
        assert chunks[2]["choices"][0]["delta"]["content"] == "world"
        assert chunks[-1]["choices"][0]["finish_reason"] == "stop"

    async def test_skips_empty_text_deltas(self):
        events = _aiter([
            {"type": "text_delta", "text": ""},
            {"type": "text_delta", "text": "x"},
            {"type": "done", "result": AgentResult()},
        ])
        chunks = [c async for c in agent_events_to_openai_chunks(events, "m")]
        contents = [
            c["choices"][0]["delta"].get("content")
            for c in chunks
            if c["choices"][0]["delta"].get("content")
        ]
        assert contents == ["x"]

    async def test_error_event_sets_finish_reason(self):
        events = _aiter([
            {"type": "text_delta", "text": "partial"},
            {"type": "error", "message": "boom"},
        ])
        chunks = [c async for c in agent_events_to_openai_chunks(events, "m")]
        assert chunks[-1]["choices"][0]["finish_reason"] == "error"

    async def test_chunk_id_is_stable_across_chunks(self):
        events = _aiter([
            {"type": "text_delta", "text": "a"},
            {"type": "text_delta", "text": "b"},
            {"type": "done", "result": AgentResult()},
        ])
        chunks = [c async for c in agent_events_to_openai_chunks(events, "m")]
        ids = {c["id"] for c in chunks}
        assert len(ids) == 1

    async def test_tool_call_surfaces_metadata(self):
        events = _aiter([
            {"type": "tool_call", "tool_name": "bash"},
            {"type": "done", "result": AgentResult()},
        ])
        chunks = [c async for c in agent_events_to_openai_chunks(events, "m")]
        contents = [
            c["choices"][0]["delta"].get("content")
            for c in chunks
            if c["choices"][0]["delta"].get("content")
        ]
        assert any("tool:bash" in c for c in contents)

    async def test_agent_event_dataclass_support(self):
        events = _aiter([
            AgentEvent(type="text_delta", text="z"),
            AgentEvent(type="done", result=AgentResult()),
        ])
        chunks = [c async for c in agent_events_to_openai_chunks(events, "m")]
        content_chunks = [
            c for c in chunks if c["choices"][0]["delta"].get("content")
        ]
        assert content_chunks[0]["choices"][0]["delta"]["content"] == "z"

    async def test_coerces_non_dict_events(self):
        events = _aiter(["plain-string"])
        chunks = [c async for c in agent_events_to_openai_chunks(events, "m")]
        # role, delta for the string, terminal
        assert chunks[1]["choices"][0]["delta"]["content"] == "plain-string"


class TestSseLines:
    async def test_sse_lines_end_with_done(self):
        events = _aiter([
            {"type": "text_delta", "text": "hi"},
            {"type": "done", "result": AgentResult()},
        ])
        lines = [line async for line in agent_events_to_sse_lines(events, "m")]
        assert lines[-1] == "[DONE]"
        # Each non-terminal line is parseable JSON
        for line in lines[:-1]:
            obj = json.loads(line)
            assert obj["object"] == "chat.completion.chunk"


class TestChunkShape:
    def test_role_chunk_structure(self):
        c = _role_chunk("id-1", "m")
        assert c["choices"][0]["delta"]["role"] == "assistant"
        assert c["choices"][0]["finish_reason"] is None
        assert c["object"] == "chat.completion.chunk"

    def test_final_chunk_has_finish_reason(self):
        c = _final_chunk("id-1", "m", "stop")
        assert c["choices"][0]["finish_reason"] == "stop"
