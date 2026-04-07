"""Inline persona executor for /orchestrate.

Wraps a ConversationRuntime so OrchestratorHook can dispatch each selected
persona as a one-shot LLM call (system prompt + task) instead of spawning
real swarm members. Small enough to unit-test without a full session.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from llm_code.swarm.personas import AgentPersona


async def inline_persona_executor(
    runtime: Any, persona: AgentPersona, task: str
) -> tuple[bool, str]:
    """Run *task* against *persona* via a one-shot provider call.

    Returns ``(True, text)`` on success, ``(False, repr(exc))`` on failure.
    Model resolution falls back to the runtime default — model_hint routing
    is intentionally out of scope here.
    """
    try:
        from llm_code.api.types import Message, MessageRequest, TextBlock

        model = getattr(getattr(runtime, "_config", None), "model", "") or ""
        request = MessageRequest(
            model=model,
            messages=(Message(role="user", content=(TextBlock(text=task),)),),
            system=persona.system_prompt,
            max_tokens=2048,
            temperature=persona.temperature,
            stream=False,
        )
        response = await runtime._provider.send_message(request)
        text = response.content[0].text if response.content else ""
        return True, text
    except Exception as exc:  # noqa: BLE001
        return False, repr(exc)


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
