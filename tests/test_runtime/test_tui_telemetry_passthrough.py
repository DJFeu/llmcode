"""Smoke test: AppState.from_config passes RuntimeConfig.telemetry directly into Telemetry().

Pre-M10: the bridging block lived in ``tui/runtime_init.py`` and the
test asserted against ``Telemetry(self._app._config.telemetry)``.

M10.3: body moved to ``runtime/app_state.py`` using
``Telemetry(config.telemetry)``; the ``tui/runtime_init.py`` adapter
was kept as a thin shim.

M11.3: the entire ``tui/`` package was deleted, taking the adapter
with it. The regression this test guards against is unchanged: never
reconstruct a ``TelemetryConfig(enabled=..., endpoint=..., ...)`` on
the way in, just pass the already-typed ``config.telemetry`` through.
"""
from __future__ import annotations

import inspect

import llm_code.runtime.app_state as app_state


def test_app_state_passes_config_telemetry_directly() -> None:
    """The bridging block must call Telemetry(config.telemetry), not
    reconstruct via TelemetryConfig(enabled=..., endpoint=..., ...)."""
    src = inspect.getsource(app_state)
    assert "Telemetry(config.telemetry)" in src


def test_app_state_does_not_rebuild_telemetry_config() -> None:
    """Regression guard: AppState.from_config must pass
    config.telemetry straight through without wrapping it."""
    src = inspect.getsource(app_state)
    assert "TelemetryConfig(" not in src
    assert "endpoint=config.telemetry.endpoint" not in src
