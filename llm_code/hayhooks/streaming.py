"""Stream adapter — agent events → OpenAI chunks / MCP notifications."""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from typing import Any, AsyncIterable, AsyncIterator


@dataclass(frozen=True)
class AgentEvent:
    """Normalised agent event shape used by the adapter tests."""

    type: str
    text: str = ""
    tool_name: str = ""
    args: dict | None = None
    output: str = ""
    message: str = ""
    result: Any = None


def _chat_id() -> str:
    return f"chatcmpl-{uuid.uuid4().hex[:24]}"


def _base_chunk(chat_id: str, model: str) -> dict:
    return {
        "id": chat_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
    }


def _delta_chunk(chat_id: str, model: str, content: str) -> dict:
    chunk = _base_chunk(chat_id, model)
    chunk["choices"] = [
        {
            "index": 0,
            "delta": {"content": content},
            "finish_reason": None,
        }
    ]
    return chunk


def _role_chunk(chat_id: str, model: str) -> dict:
    chunk = _base_chunk(chat_id, model)
    chunk["choices"] = [
        {
            "index": 0,
            "delta": {"role": "assistant"},
            "finish_reason": None,
        }
    ]
    return chunk


def _final_chunk(chat_id: str, model: str, finish_reason: str) -> dict:
    chunk = _base_chunk(chat_id, model)
    chunk["choices"] = [
        {
            "index": 0,
            "delta": {},
            "finish_reason": finish_reason,
        }
    ]
    return chunk


def _event_to_dict(event: Any) -> dict:
    if isinstance(event, dict):
        return event
    if isinstance(event, AgentEvent):
        return {
            "type": event.type,
            "text": event.text,
            "tool_name": event.tool_name,
            "args": event.args or {},
            "output": event.output,
            "message": event.message,
            "result": event.result,
        }
    return {"type": "text_delta", "text": str(event)}


async def agent_events_to_openai_chunks(
    events: AsyncIterable[Any],
    model: str,
    chat_id: str | None = None,
) -> AsyncIterator[dict]:
    """Yield OpenAI-shape chunks for each agent event.

    Emits in order:
    - opening role chunk (``delta.role = "assistant"``)
    - one content chunk per text_delta
    - terminal chunk with ``finish_reason`` (``stop`` by default)
    """
    cid = chat_id or _chat_id()
    yield _role_chunk(cid, model)
    finish_reason = "stop"
    async for raw in events:
        evt = _event_to_dict(raw)
        etype = evt.get("type")
        if etype == "text_delta":
            text = evt.get("text", "")
            if text:
                yield _delta_chunk(cid, model, text)
        elif etype == "tool_call":
            # Surface tool activity as a meta-delta so callers can track it.
            name = evt.get("tool_name") or evt.get("name", "")
            yield _delta_chunk(cid, model, f"\n[tool:{name}]\n")
        elif etype == "error":
            finish_reason = "error"
            break
        elif etype == "done":
            result = evt.get("result")
            if result is not None:
                finish_reason = getattr(result, "exit_reason", "stop") or "stop"
            break
    yield _final_chunk(cid, model, finish_reason)


async def agent_events_to_sse_lines(
    events: AsyncIterable[Any],
    model: str,
    chat_id: str | None = None,
) -> AsyncIterator[str]:
    """Serialise chunks for ``sse-starlette.EventSourceResponse``."""
    async for chunk in agent_events_to_openai_chunks(events, model, chat_id):
        yield json.dumps(chunk, ensure_ascii=False)
    yield "[DONE]"


async def agent_events_to_mcp_notifications(
    events: AsyncIterable[Any],
    send_notification,
) -> AsyncIterator[None]:
    """Forward each text_delta event as an MCP progress notification.

    ``send_notification`` is an async callable ``(method: str, params: dict)``
    — usually ``server.request_context.session.send_progress_notification``.
    """
    async for raw in events:
        evt = _event_to_dict(raw)
        if evt.get("type") == "text_delta" and evt.get("text"):
            try:
                await send_notification(
                    "notifications/progress",
                    {"progress": evt["text"]},
                )
            except Exception:
                # Notifications are best-effort.
                pass
        yield None
