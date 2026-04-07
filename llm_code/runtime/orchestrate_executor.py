"""Inline persona executor for /orchestrate.

Wraps a ConversationRuntime so OrchestratorHook can dispatch each selected
persona as a one-shot LLM call (system prompt + task) instead of spawning
real swarm members. Small enough to unit-test without a full session.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable
from uuid import uuid4

from llm_code.swarm.personas import AgentPersona


async def inline_persona_executor(
    runtime: Any, persona: AgentPersona, task: str
) -> tuple[bool, str]:
    """Run *task* against *persona* via a one-shot provider call.

    Returns ``(True, text)`` on success, ``(False, repr(exc))`` on failure.
    Model resolution falls back to the runtime default — model_hint routing
    is intentionally out of scope here.

    Any MCP servers the persona spawned under its agent_id are torn down in
    a finally block so root-owned servers survive the attempt.
    """
    agent_id = f"persona-{persona.name}-{uuid4().hex[:8]}"
    try:
        from llm_code.api.types import Message, MessageRequest, TextBlock
        from llm_code.runtime.fork_cache import derive_fork_key

        model = getattr(getattr(runtime, "_config", None), "model", "") or ""
        parent_session_id = getattr(getattr(runtime, "session", None), "session_id", "") or ""
        cache_key = derive_fork_key(parent_session_id, persona.name)
        request = MessageRequest(
            model=model,
            messages=(Message(role="user", content=(TextBlock(text=task),)),),
            system=persona.system_prompt,
            max_tokens=2048,
            temperature=persona.temperature,
            stream=False,
            cache_key=cache_key,
        )
        response = await runtime._provider.send_message(request)
        text = response.content[0].text if response.content else ""
        return True, text
    except Exception as exc:  # noqa: BLE001
        return False, repr(exc)
    finally:
        mcp_manager = getattr(runtime, "_mcp_manager", None)
        if mcp_manager is not None and hasattr(mcp_manager, "cleanup_for_agent"):
            try:
                await mcp_manager.cleanup_for_agent(agent_id)
            except Exception:  # noqa: BLE001
                pass


def make_inline_persona_executor(
    runtime: Any,
) -> Callable[[AgentPersona, str], Awaitable[tuple[bool, str]]]:
    """Bind *runtime* into an async executor."""

    async def _exec(persona: AgentPersona, task: str) -> tuple[bool, str]:
        return await inline_persona_executor(runtime, persona, task)

    return _exec


def sync_wrap(
    async_executor: Callable[[AgentPersona, str], Awaitable[tuple[bool, str]]],
) -> Callable[[AgentPersona, str], tuple[bool, str]]:
    """Adapt an async executor to OrchestratorHook's sync signature.

    OrchestratorHook.orchestrate is sync and is invoked via asyncio.to_thread
    in the TUI worker, so this thread has no running loop and asyncio.run
    is safe.
    """
    import asyncio

    def _sync(persona: AgentPersona, task: str) -> tuple[bool, str]:
        return asyncio.run(async_executor(persona, task))

    return _sync
