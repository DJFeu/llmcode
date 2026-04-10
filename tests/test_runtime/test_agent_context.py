"""Tests for agent context isolation via contextvars."""
from __future__ import annotations

import asyncio

import pytest

from llm_code.runtime.agent_context import (
    AgentContext,
    arun_with_agent_context,
    get_agent_context,
    get_agent_id,
    run_with_agent_context,
)


class TestAgentContext:
    def test_default_is_none(self) -> None:
        assert get_agent_context() is None
        assert get_agent_id() is None

    def test_sync_context_isolation(self) -> None:
        ctx = AgentContext(agent_id="agent-1", name="test")
        result = run_with_agent_context(ctx, get_agent_id)
        assert result == "agent-1"
        # After return, context is reset
        assert get_agent_id() is None

    def test_sync_context_resets_on_exception(self) -> None:
        ctx = AgentContext(agent_id="agent-2")

        with pytest.raises(RuntimeError):
            def bomb():
                assert get_agent_id() == "agent-2"
                raise RuntimeError("boom")
            run_with_agent_context(ctx, bomb)

        assert get_agent_id() is None

    def test_nested_contexts(self) -> None:
        outer = AgentContext(agent_id="outer")
        inner = AgentContext(agent_id="inner")

        def check_inner():
            assert get_agent_id() == "inner"

        def check_outer():
            assert get_agent_id() == "outer"
            run_with_agent_context(inner, check_inner)
            # Outer restored after inner completes
            assert get_agent_id() == "outer"

        run_with_agent_context(outer, check_outer)
        assert get_agent_id() is None

    def test_consume_invocation_once(self) -> None:
        ctx = AgentContext(
            agent_id="x", invocation_kind="spawn",
        )
        assert ctx.consume_invocation() == "spawn"
        assert ctx.consume_invocation() is None  # consumed

    def test_consume_invocation_resume(self) -> None:
        ctx = AgentContext(
            agent_id="x", invocation_kind="resume",
        )
        assert ctx.consume_invocation() == "resume"
        assert ctx.consume_invocation() is None


class TestAsyncContextIsolation:
    def test_async_isolation(self) -> None:
        async def run():
            ctx = AgentContext(agent_id="async-1")
            result = await arun_with_agent_context(
                ctx, _async_get_id(),
            )
            assert result == "async-1"
            assert get_agent_id() is None

        asyncio.run(run())

    def test_concurrent_agents_isolated(self) -> None:
        """Two agents running concurrently must not cross-contaminate."""
        async def agent_work(agent_id: str, delay: float) -> str:
            ctx = AgentContext(agent_id=agent_id)

            async def work():
                # Yield control to let the other agent run
                await asyncio.sleep(delay)
                return get_agent_id()

            return await arun_with_agent_context(ctx, work())

        async def run():
            # Run two agents concurrently
            results = await asyncio.gather(
                agent_work("A", 0.01),
                agent_work("B", 0.0),
            )
            # Each agent should see its own ID, not the other's
            assert set(results) == {"A", "B"}

        asyncio.run(run())

    def test_async_resets_on_exception(self) -> None:
        async def run():
            ctx = AgentContext(agent_id="fail")

            async def bomb():
                raise ValueError("async boom")

            with pytest.raises(ValueError):
                await arun_with_agent_context(ctx, bomb())

            assert get_agent_id() is None

        asyncio.run(run())


async def _async_get_id() -> str | None:
    return get_agent_id()
