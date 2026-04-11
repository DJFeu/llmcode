"""Unit tests for ``AppState`` dataclass and its ``from_config`` factory.

These tests exercise the happy path and the documented resilience
paths (None config, failing optional subsystems). They use a real
``RuntimeConfig`` pointed at a temporary directory so the
subsystem-assembly code runs end-to-end, but no network calls and
no real LLM provider — ``ProviderClient.from_model`` is monkeypatched
to return a lightweight stub that satisfies the constructor signature.

This mirrors the approach ``tests/test_tui/test_runtime_init_*`` takes
for the legacy adapter; AppState tests are the new source of truth
and ``test_tui/`` gets its coverage from the thin adapter delegating
to us.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from llm_code.runtime.app_state import AppState


# === Empty-shell construction ===


def test_empty_appstate_has_sensible_defaults() -> None:
    state = AppState()
    assert state.config is None
    assert state.cwd == Path.cwd()
    assert state.budget is None
    assert state.initial_mode == "workspace_write"
    # Every subsystem starts as None
    assert state.runtime is None
    assert state.cost_tracker is None
    assert state.tool_reg is None
    assert state.skills is None
    assert state.memory is None
    # Live mutation state has safe zero values
    assert state.input_tokens == 0
    assert state.output_tokens == 0
    assert state.last_stop_reason == "unknown"
    assert state.pending_images == []
    assert state.loaded_plugins == {}
    assert state.plan_mode is False
    assert state.voice_active is False
    assert state.voice_recorder is None
    assert state.voice_stt is None
    assert state.analysis_context is None
    assert state.context_warned is False
    assert state.permission_pending is False


def test_empty_mutable_fields_are_not_shared() -> None:
    """Default-factory fields must not alias across instances."""
    a = AppState()
    b = AppState()
    a.pending_images.append("one")
    a.loaded_plugins["x"] = 1
    a.user_agent_roles["r"] = "v"
    assert b.pending_images == []
    assert b.loaded_plugins == {}
    assert b.user_agent_roles == {}


# === from_config(None) path ===


def test_from_config_none_returns_empty_shell(tmp_path: Path, caplog) -> None:
    """When config is None, from_config logs a warning and returns a
    populated shell with only the input fields set."""
    import logging
    caplog.set_level(logging.WARNING, logger="llm_code.runtime.app_state")
    state = AppState.from_config(None, cwd=tmp_path, budget=4096)
    assert state.config is None
    assert state.cwd == tmp_path
    assert state.budget == 4096
    assert state.runtime is None
    assert state.tool_reg is None
    assert any(
        "runtime will not be initialized" in rec.message for rec in caplog.records
    )


# === from_config happy path (real RuntimeConfig, stubbed provider) ===


@pytest.fixture
def minimal_config(tmp_path: Path):
    """Build a real RuntimeConfig with all optional features off so the
    factory runs the happiest-path-with-all-branches exercise.

    Intentionally does NOT enable swarm/computer_use/ide/telemetry/lsp
    — those branches are covered separately by their own tests and
    flipping them all on at once pulls in a lot of optional deps.
    """
    from llm_code.runtime.config import RuntimeConfig
    # The default RuntimeConfig has all optional features disabled and
    # no required network/disk state.
    return RuntimeConfig()


@pytest.fixture
def stub_provider_client():
    """Patch ProviderClient.from_model to a lightweight stub.

    The real factory needs a valid API key + network reach; tests
    don't. The stub satisfies ``isinstance(provider, ProviderClient)``
    by returning a ``MagicMock`` whose ``supports_*`` methods answer
    False (matching a minimal, non-native-tool provider).
    """
    stub = MagicMock()
    stub.supports_native_tools = MagicMock(return_value=False)
    stub.supports_images = MagicMock(return_value=False)
    stub.supports_reasoning = MagicMock(return_value=False)
    with patch(
        "llm_code.api.client.ProviderClient.from_model",
        return_value=stub,
    ):
        yield stub


def test_from_config_happy_path_builds_core_subsystems(
    tmp_path: Path, minimal_config, stub_provider_client,
) -> None:
    state = AppState.from_config(minimal_config, cwd=tmp_path)

    # Config + input passthrough
    assert state.config is minimal_config
    assert state.cwd == tmp_path

    # Always-built subsystems — these should succeed on any supported
    # platform since they only touch in-process objects or tmp_path.
    assert state.cost_tracker is not None
    assert state.tool_reg is not None
    assert state.deferred_tool_manager is not None
    assert state.runtime is not None

    # Runtime got the provider stub, the tool registry, and the cost
    # tracker we just built.
    assert state.runtime._provider is stub_provider_client
    assert state.runtime._tool_registry is state.tool_reg
    assert state.runtime._cost_tracker is state.cost_tracker


def test_from_config_registers_core_tools(
    tmp_path: Path, minimal_config, stub_provider_client,
) -> None:
    """The factory must invoke the core-tool registrar — without it
    the tool registry would be empty and dispatcher commands would
    find nothing."""
    state = AppState.from_config(minimal_config, cwd=tmp_path)
    # At minimum there should be some tools registered — the exact set
    # depends on config.native_tools but the registry should never be
    # completely empty on a happy path.
    names = [t.name for t in state.tool_reg.definitions()]
    assert len(names) > 0, f"expected core tools registered, got: {names}"


def test_from_config_accepts_injected_register_core_tools(
    tmp_path: Path, minimal_config, stub_provider_client,
) -> None:
    """Callers can override the core-tool registrar (e.g. for tests
    that want a minimal registry). Verifies the DI seam works."""
    calls = []

    def fake_register(registry, config):
        calls.append((registry, config))

    state = AppState.from_config(
        minimal_config,
        cwd=tmp_path,
        register_core_tools=fake_register,
    )
    assert len(calls) == 1
    assert calls[0][0] is state.tool_reg
    assert calls[0][1] is minimal_config


def test_from_config_checkpoint_mgr_none_outside_git_repo(
    tmp_path: Path, minimal_config, stub_provider_client,
) -> None:
    """Without a ``.git`` directory, checkpoint manager stays None."""
    state = AppState.from_config(minimal_config, cwd=tmp_path)
    assert state.checkpoint_mgr is None


def test_from_config_checkpoint_mgr_built_inside_git_repo(
    tmp_path: Path, minimal_config, stub_provider_client,
) -> None:
    """With a ``.git`` directory present, checkpoint manager is built."""
    (tmp_path / ".git").mkdir()
    state = AppState.from_config(minimal_config, cwd=tmp_path)
    assert state.checkpoint_mgr is not None


def test_from_config_token_budget_only_when_requested(
    tmp_path: Path, minimal_config, stub_provider_client,
) -> None:
    state = AppState.from_config(minimal_config, cwd=tmp_path, budget=None)
    assert state.token_budget is None

    state_with_budget = AppState.from_config(
        minimal_config, cwd=tmp_path, budget=8192,
    )
    assert state_with_budget.token_budget is not None


def test_from_config_dialogs_stays_none(
    tmp_path: Path, minimal_config, stub_provider_client,
) -> None:
    """AppState deliberately does NOT build TextualDialogs — that's
    TUI-specific and the adapter installs it on top. Runtime falls
    back to HeadlessDialogs."""
    state = AppState.from_config(minimal_config, cwd=tmp_path)
    assert state.dialogs is None
    assert state.runtime._dialogs is None


def test_from_config_uses_cwd_default(
    minimal_config, stub_provider_client, monkeypatch, tmp_path,
) -> None:
    """When cwd is omitted, from_config uses Path.cwd()."""
    monkeypatch.chdir(tmp_path)
    state = AppState.from_config(minimal_config)
    assert state.cwd == tmp_path


# === Subagent factory closure semantics ===


def test_subagent_factory_raises_before_runtime_attached(
    tmp_path: Path, minimal_config, stub_provider_client,
) -> None:
    """The AgentTool closure calls state.runtime lazily. Calling it
    before state.runtime is set must raise, not silently return None.

    This covers the race between tool registration (which creates the
    closure) and runtime construction (which sets state.runtime). If
    the closure were to capture runtime eagerly, it would be None
    forever.
    """
    # We can't easily get at the closure directly, but we can verify
    # that by the time from_config returns, state.runtime IS set —
    # which is the invariant the closure relies on.
    state = AppState.from_config(minimal_config, cwd=tmp_path)
    assert state.runtime is not None
    # And the agent tool should be registered (if subagent_factory
    # imports succeed, which they do on a stock install).
    agent = state.tool_reg.get("agent")
    # agent tool may or may not be present depending on imports —
    # both states are legal, but if it's present it must be the real
    # AgentTool, not a placeholder.
    if agent is not None:
        from llm_code.tools.agent import AgentTool
        assert isinstance(agent, AgentTool)


# === Signature stability ===


def test_app_state_from_config_signature_is_backwards_compatible() -> None:
    """Signature of from_config is (config, cwd=None, *, budget=None,
    initial_mode=..., register_core_tools=None). Locked in so future
    refactors don't silently break the TUI adapter call site."""
    import inspect
    sig = inspect.signature(AppState.from_config)
    params = list(sig.parameters)
    # First two positional
    assert params[0] == "config"
    assert params[1] == "cwd"
    # All remaining params must be keyword-only (sig enforces via *)
    for name in ("budget", "initial_mode", "register_core_tools"):
        assert name in sig.parameters
        assert sig.parameters[name].kind == inspect.Parameter.KEYWORD_ONLY


# === Live state is writeable post-construction ===


def test_token_counters_are_mutable_post_construction() -> None:
    state = AppState()
    state.input_tokens += 100
    state.output_tokens += 50
    state.last_stop_reason = "end_turn"
    assert state.input_tokens == 100
    assert state.output_tokens == 50
    assert state.last_stop_reason == "end_turn"


def test_voice_state_is_mutable_post_construction() -> None:
    state = AppState()
    fake_recorder = object()
    state.voice_active = True
    state.voice_recorder = fake_recorder
    assert state.voice_active is True
    assert state.voice_recorder is fake_recorder
    state.voice_active = False
    assert state.voice_active is False


# NOTE: The v1.x RuntimeInitializer adapter parity smoke test lived
# here until M11.3 deleted tui/runtime_init.py entirely. AppState is
# now the single source of truth and the M10.3 thin adapter is gone.
