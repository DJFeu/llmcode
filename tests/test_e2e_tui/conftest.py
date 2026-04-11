"""Shared fixtures for Textual pilot E2E tests.

The goal of every fixture in this file is to make spinning up a
real :class:`LLMCodeTUI` inside ``App.run_test()`` as cheap as a
unit test — no MCP servers started, no LLM provider instantiated,
no real config on disk, no sessions persisted. Tests that need one
of those things should opt in explicitly by un-mocking it.
"""
from __future__ import annotations


import pytest


# ── Minimal config factory ─────────────────────────────────────────────


def _make_test_config(**overrides):
    """Build a :class:`RuntimeConfig` with every field at its default.

    Individual tests can override specific fields via keyword args
    (e.g. ``voice=VoiceConfig(enabled=True)``). Returned config is
    frozen-dataclass-safe — no mutation after construction.
    """
    import dataclasses

    from llm_code.runtime.config import RuntimeConfig

    cfg = RuntimeConfig()
    if overrides:
        cfg = dataclasses.replace(cfg, **overrides)
    return cfg


@pytest.fixture
def tui_config():
    """Default config for a pilot run — no voice, no telemetry, no MCP."""
    return _make_test_config()


@pytest.fixture
def tui_voice_config():
    """Config with voice enabled (local backend) for voice flow tests."""
    from llm_code.runtime.config_features import VoiceConfig

    return _make_test_config(
        voice=VoiceConfig(
            enabled=True,
            backend="local",
            local_model="base",
            language="en",
            hotkey="ctrl+g",
            silence_seconds=2.0,
            silence_threshold=3000,
        )
    )


# ── Pilot app factory ──────────────────────────────────────────────────


@pytest.fixture
async def pilot_app(tui_config, tmp_path, monkeypatch):
    """Boot an LLMCodeTUI inside ``App.run_test()`` with runtime
    initialization stubbed out.

    Yields ``(app, pilot)`` so scenarios can drive keystrokes via
    ``pilot.press(...)`` and assert on the real widget tree.
    """
    async for pair in _pilot_app_impl(tui_config, tmp_path, monkeypatch):
        yield pair


@pytest.fixture
async def pilot_voice_app(tui_voice_config, tmp_path, monkeypatch):
    """Like ``pilot_app`` but with voice enabled in config."""
    async for pair in _pilot_app_impl(tui_voice_config, tmp_path, monkeypatch):
        yield pair


async def _pilot_app_impl(config, tmp_path, monkeypatch):
    from llm_code.tui.app import LLMCodeTUI

    # Stub _init_runtime so we don't boot the tool registry, MCP
    # manager, session layer, provider, or skills on every test.
    # The UI layer is what we want to exercise.
    monkeypatch.setattr(LLMCodeTUI, "_init_runtime", lambda self: None)

    # Stub the async MCP init worker — we have no MCP servers to
    # contact and we don't want background tasks leaking into the
    # pilot's asyncio loop.
    async def _noop_mcp(self):  # pragma: no cover - trivial stub
        return None

    monkeypatch.setattr(LLMCodeTUI, "_init_mcp", _noop_mcp)

    # Silence the SIGINT handler registration — pytest owns the
    # signal in the test process.
    import signal

    monkeypatch.setattr(signal, "signal", lambda *args, **kwargs: None)

    app = LLMCodeTUI(config=config, cwd=tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        # on_mount already ran by the time run_test() entered the
        # context manager. Give it one more pause tick so reactive
        # watchers and scheduled timers settle.
        await pilot.pause()
        yield app, pilot
