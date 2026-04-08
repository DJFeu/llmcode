"""Pin the invariant that TelemetryConfig is owned by the telemetry module
and merely re-exported from config.

If a future refactor accidentally re-introduces a duplicate dataclass in
config.py, this test fails immediately instead of silently allowing field
drift between the two copies.
"""
from __future__ import annotations

from llm_code.runtime.config import TelemetryConfig as ConfigSide
from llm_code.runtime.telemetry import TelemetryConfig as TelemetrySide


def test_telemetry_config_is_a_single_class() -> None:
    """Both import paths must resolve to the exact same class object."""
    assert ConfigSide is TelemetrySide


def test_runtime_config_telemetry_field_uses_canonical_class() -> None:
    """RuntimeConfig.telemetry must hold an instance of the canonical class."""
    from llm_code.runtime.config import RuntimeConfig
    cfg = RuntimeConfig()
    assert isinstance(cfg.telemetry, TelemetrySide)


def test_construction_via_either_import_yields_equivalent_instances() -> None:
    """Constructing via either import path produces equal dataclass instances."""
    a = ConfigSide(enabled=True, endpoint="http://x:4318", service_name="x")
    b = TelemetrySide(enabled=True, endpoint="http://x:4318", service_name="x")
    assert a == b


def test_no_field_drift_between_import_paths() -> None:
    """Defensive: both imports must expose the same dataclass fields."""
    import dataclasses

    a_fields = {f.name for f in dataclasses.fields(ConfigSide)}
    b_fields = {f.name for f in dataclasses.fields(TelemetrySide)}
    assert a_fields == b_fields
