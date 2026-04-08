"""Tests for lazy / scoped MCP spawning (personas + skills + config split).

Covers:
- MCPConfig parsing of new split schema and legacy flat schema.
- always_on vs on_demand separation at session start.
- Persona with mcp_servers triggers start_server before run.
- Persona MCP cleanup fires on both success and failure paths.
- Skill with mcp_servers spawns at first run_turn.
- Approval denied → warning logged, persona still runs.
- Missing on_demand config for declared server → warning, no crash.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from llm_code.runtime.config import MCPConfig, _parse_mcp_config
from llm_code.runtime.orchestrate_executor import inline_persona_executor
from llm_code.runtime.skills import Skill
from llm_code.swarm.personas import AgentPersona


# ---------------------------------------------------------------------------
# Config parsing
# ---------------------------------------------------------------------------


class TestMCPConfigParsing:
    def test_new_schema_split(self) -> None:
        raw = {
            "always_on": {"fs": {"command": "fs"}},
            "on_demand": {"tavily": {"command": "tavily"}},
        }
        cfg = _parse_mcp_config(raw)
        assert isinstance(cfg, MCPConfig)
        assert "fs" in cfg.always_on
        assert "tavily" in cfg.on_demand
        assert "tavily" not in cfg.always_on

    def test_legacy_flat_schema_all_always_on(self) -> None:
        raw = {"fs": {"command": "fs"}, "git": {"command": "git"}}
        cfg = _parse_mcp_config(raw)
        assert set(cfg.always_on) == {"fs", "git"}
        assert cfg.on_demand == {}

    def test_empty(self) -> None:
        assert _parse_mcp_config({}) == MCPConfig()
        assert _parse_mcp_config(None) == MCPConfig()  # type: ignore[arg-type]

    def test_only_on_demand(self) -> None:
        cfg = _parse_mcp_config({"on_demand": {"x": {"command": "x"}}})
        assert cfg.always_on == {}
        assert "x" in cfg.on_demand


# ---------------------------------------------------------------------------
# Persona MCP spawn via inline_persona_executor
# ---------------------------------------------------------------------------


class _StubResponse:
    def __init__(self, text: str = "ok") -> None:
        block = MagicMock()
        block.text = text
        self.content = (block,)


def _make_runtime(
    *,
    on_demand: dict[str, dict] | None = None,
    mcp_manager: Any = None,
    provider_result: Any = None,
    provider_raises: Exception | None = None,
) -> Any:
    runtime = MagicMock()
    runtime._config = MagicMock()
    runtime._config.model = "stub-model"
    runtime._config.mcp = MCPConfig(always_on={}, on_demand=dict(on_demand or {}))
    runtime.session = MagicMock()
    runtime.session.session_id = "sess-1"
    runtime._mcp_manager = mcp_manager
    runtime.request_mcp_approval = AsyncMock(return_value=True)
    provider = MagicMock()
    if provider_raises is not None:
        provider.send_message = AsyncMock(side_effect=provider_raises)
    else:
        provider.send_message = AsyncMock(
            return_value=provider_result or _StubResponse("done")
        )
    runtime._provider = provider
    return runtime


def _make_mcp_manager() -> MagicMock:
    mgr = MagicMock()
    mgr.start_server = AsyncMock()
    mgr.cleanup_for_agent = AsyncMock()
    return mgr


_WEB = AgentPersona(
    name="web",
    description="test",
    system_prompt="sys" * 50,
    mcp_servers=("tavily",),
)


@pytest.mark.asyncio
async def test_persona_mcp_spawn_on_success() -> None:
    mgr = _make_mcp_manager()
    runtime = _make_runtime(
        on_demand={"tavily": {"command": "npx", "args": ["-y", "tavily-mcp"]}},
        mcp_manager=mgr,
    )
    ok, _ = await inline_persona_executor(runtime, _WEB, "find something")
    assert ok is True
    mgr.start_server.assert_awaited_once()
    args, kwargs = mgr.start_server.call_args
    assert args[0] == "tavily"
    assert kwargs["owner_agent_id"].startswith("persona-web-")
    mgr.cleanup_for_agent.assert_awaited_once()


@pytest.mark.asyncio
async def test_persona_mcp_cleanup_on_failure() -> None:
    mgr = _make_mcp_manager()
    runtime = _make_runtime(
        on_demand={"tavily": {"command": "tavily"}},
        mcp_manager=mgr,
        provider_raises=RuntimeError("boom"),
    )
    ok, err = await inline_persona_executor(runtime, _WEB, "find something")
    assert ok is False
    assert "boom" in err
    mgr.cleanup_for_agent.assert_awaited_once()


@pytest.mark.asyncio
async def test_persona_mcp_missing_config_does_not_crash(caplog) -> None:
    mgr = _make_mcp_manager()
    runtime = _make_runtime(on_demand={}, mcp_manager=mgr)
    caplog.set_level(logging.WARNING)
    ok, _ = await inline_persona_executor(runtime, _WEB, "go")
    assert ok is True
    mgr.start_server.assert_not_called()
    assert any("not declared" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_persona_mcp_approval_denied_still_runs(caplog) -> None:
    from llm_code.mcp.manager import MCPApprovalDeniedError

    mgr = _make_mcp_manager()
    mgr.start_server = AsyncMock(side_effect=MCPApprovalDeniedError("nope"))
    runtime = _make_runtime(
        on_demand={"tavily": {"command": "tavily"}},
        mcp_manager=mgr,
    )
    caplog.set_level(logging.WARNING)
    ok, _ = await inline_persona_executor(runtime, _WEB, "go")
    assert ok is True  # persona still runs in degraded mode
    assert any("spawn failed" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_persona_without_mcp_servers_noop() -> None:
    persona = AgentPersona(
        name="plain", description="d", system_prompt="p" * 200
    )
    mgr = _make_mcp_manager()
    runtime = _make_runtime(mcp_manager=mgr)
    ok, _ = await inline_persona_executor(runtime, persona, "task")
    assert ok is True
    mgr.start_server.assert_not_called()


# ---------------------------------------------------------------------------
# Skill MCP spawn at first run_turn
# ---------------------------------------------------------------------------


@dataclass
class _StubSkillSet:
    auto_skills: tuple = ()
    command_skills: tuple = ()


@pytest.mark.asyncio
async def test_skill_mcp_spawn_at_first_turn() -> None:
    from llm_code.runtime.conversation import ConversationRuntime

    skill = Skill(
        name="research",
        description="x",
        content="",
        mcp_servers=("tavily",),
    )
    skillset = _StubSkillSet(auto_skills=(), command_skills=(skill,))

    mgr = _make_mcp_manager()

    # Build a minimal runtime by bypassing __init__ and setting the
    # attributes _spawn_pending_skill_mcp_servers touches.
    runtime = ConversationRuntime.__new__(ConversationRuntime)
    runtime._mcp_manager = mgr
    runtime._skill_mcp_spawned = False
    runtime._pending_skill_mcp_spawns = [("research", "tavily")]
    cfg = MagicMock()
    cfg.mcp = MCPConfig(
        on_demand={"tavily": {"command": "npx", "args": ["-y", "tavily-mcp"]}}
    )
    runtime._config = cfg
    runtime.request_mcp_approval = AsyncMock(return_value=True)

    await runtime._spawn_pending_skill_mcp_servers()
    mgr.start_server.assert_awaited_once()
    kwargs = mgr.start_server.call_args.kwargs
    assert kwargs["owner_agent_id"] == "skill:research"

    # Idempotent — second call is a no-op.
    await runtime._spawn_pending_skill_mcp_servers()
    mgr.start_server.assert_awaited_once()


@pytest.mark.asyncio
async def test_skill_mcp_spawn_missing_config_warns(caplog) -> None:
    from llm_code.runtime.conversation import ConversationRuntime

    mgr = _make_mcp_manager()
    runtime = ConversationRuntime.__new__(ConversationRuntime)
    runtime._mcp_manager = mgr
    runtime._skill_mcp_spawned = False
    runtime._pending_skill_mcp_spawns = [("research", "ghost")]
    cfg = MagicMock()
    cfg.mcp = MCPConfig(on_demand={})
    runtime._config = cfg
    runtime.request_mcp_approval = AsyncMock(return_value=True)

    caplog.set_level(logging.WARNING)
    await runtime._spawn_pending_skill_mcp_servers()
    mgr.start_server.assert_not_called()
    assert any("not in mcp.on_demand" in r.message for r in caplog.records)
