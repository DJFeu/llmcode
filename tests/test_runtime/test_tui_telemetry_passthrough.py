"""Smoke test: tui/app.py passes RuntimeConfig.telemetry directly into Telemetry()."""
from __future__ import annotations

import inspect

import llm_code.tui.runtime_init as runtime_init


def test_tui_passes_config_telemetry_directly() -> None:
    """The bridging block must call Telemetry(config.telemetry), not
    reconstruct via TelemetryConfig(enabled=..., endpoint=..., ...)."""
    src = inspect.getsource(runtime_init)
    assert "Telemetry(self._app._config.telemetry)" in src


def test_tui_no_longer_imports_telemetry_config_in_bridge() -> None:
    """The bridging block should import only Telemetry, not TelemetryConfig."""
    src = inspect.getsource(runtime_init)
    # The line that used to manually rebuild a TelemetryConfig is gone.
    assert "TelemetryConfig(\n" not in src or "endpoint=self._config.telemetry.endpoint" not in src
