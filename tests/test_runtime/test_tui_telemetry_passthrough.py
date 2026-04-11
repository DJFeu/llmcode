"""Smoke test: AppState.from_config passes RuntimeConfig.telemetry directly into Telemetry().

Before M10.3, the bridging block lived in ``tui/runtime_init.py`` and
the test asserted against ``Telemetry(self._app._config.telemetry)``.
After M10.3, the subsystem-assembly body moved to
``runtime/app_state.py`` and the idiom is ``Telemetry(config.telemetry)``.
The regression this test guards against is unchanged: never reconstruct
a ``TelemetryConfig(enabled=..., endpoint=..., ...)`` on the way in,
just pass the already-typed ``config.telemetry`` through.
"""
from __future__ import annotations

import inspect

import llm_code.runtime.app_state as app_state
import llm_code.tui.runtime_init as runtime_init


def test_app_state_passes_config_telemetry_directly() -> None:
    """The bridging block must call Telemetry(config.telemetry), not
    reconstruct via TelemetryConfig(enabled=..., endpoint=..., ...)."""
    src = inspect.getsource(app_state)
    assert "Telemetry(config.telemetry)" in src


def test_runtime_init_adapter_does_not_rebuild_telemetry_config() -> None:
    """The legacy adapter shim must not contain any TelemetryConfig
    reconstruction either — the whole bridging block has moved to
    AppState.from_config."""
    src = inspect.getsource(runtime_init)
    assert "TelemetryConfig(" not in src
    assert "endpoint=self._config.telemetry.endpoint" not in src


def test_app_state_does_not_rebuild_telemetry_config() -> None:
    """Same regression guard, new location: AppState.from_config must
    pass config.telemetry straight through without wrapping it."""
    src = inspect.getsource(app_state)
    assert "TelemetryConfig(" not in src
    assert "endpoint=config.telemetry.endpoint" not in src
