"""Tests for llm_code.runtime.telemetry."""
from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

from llm_code.runtime.telemetry import Telemetry, TelemetryConfig, get_noop_telemetry


# ---------------------------------------------------------------------------
# TelemetryConfig defaults
# ---------------------------------------------------------------------------

class TestTelemetryConfig:
    def test_defaults(self) -> None:
        cfg = TelemetryConfig()
        assert cfg.enabled is False
        assert cfg.endpoint == "http://localhost:4318"
        assert cfg.service_name == "llm-code"

    def test_custom_values(self) -> None:
        cfg = TelemetryConfig(enabled=True, endpoint="http://otel:4318", service_name="my-app")
        assert cfg.enabled is True
        assert cfg.endpoint == "http://otel:4318"
        assert cfg.service_name == "my-app"


# ---------------------------------------------------------------------------
# No-op behaviour when disabled
# ---------------------------------------------------------------------------

class TestTelemetryNoop:
    """All methods must be safe to call when telemetry is disabled."""

    def _noop(self) -> Telemetry:
        return Telemetry(TelemetryConfig(enabled=False))

    def test_trace_turn_is_noop(self) -> None:
        t = self._noop()
        # Must not raise
        t.trace_turn("s1", "gpt-4o", 100, 50, 123.4)

    def test_trace_tool_is_noop(self) -> None:
        t = self._noop()
        t.trace_tool("bash", 10.0)
        t.trace_tool("bash", 10.0, is_error=True)

    def test_record_cost_is_noop(self) -> None:
        t = self._noop()
        t.record_cost("gpt-4o", 100, 50, 0.001)

    def test_record_error_is_noop(self) -> None:
        t = self._noop()
        t.record_error("ProviderError", "timeout after 120s")

    def test_noop_singleton(self) -> None:
        t = get_noop_telemetry()
        assert t._enabled is False
        assert get_noop_telemetry() is t


# ---------------------------------------------------------------------------
# No-op behaviour when opentelemetry is NOT installed
# ---------------------------------------------------------------------------

class TestTelemetryNoPackage:
    """Telemetry must degrade gracefully when otel packages are absent."""

    def test_enabled_but_no_package_becomes_noop(self) -> None:
        """If packages are absent, _setup raises ImportError and we fall back."""
        cfg = TelemetryConfig(enabled=True)
        # Patch _setup to raise ImportError, simulating missing packages
        with patch.object(Telemetry, "_setup", side_effect=ImportError("no otel")):
            t = Telemetry(cfg)
        # Should have been disabled gracefully
        assert t._enabled is False
        assert t._tracer is None

    def test_methods_safe_after_setup_failure(self) -> None:
        cfg = TelemetryConfig(enabled=True)
        with patch.object(Telemetry, "_setup", side_effect=ImportError("no otel")):
            t = Telemetry(cfg)
        # All public methods must be safe
        t.trace_turn("s1", "gpt-4o", 100, 50, 99.0)
        t.trace_tool("bash", 5.0, is_error=False)
        t.record_cost("gpt-4o", 100, 50, 0.001)
        t.record_error("Timeout", "took too long")


# ---------------------------------------------------------------------------
# Behaviour when otel IS installed (mocked)
# ---------------------------------------------------------------------------

class TestTelemetryWithMockedOtel:
    """Verify calls flow through to OTel API when it is available.

    Strategy: rather than fighting with mock module tree resolution, we directly
    inject mock objects into the Telemetry instance's internal attributes after
    construction.  This is the most reliable approach since the otel packages are
    not installed in the test environment.
    """

    def _make_telemetry_with_mocks(self):
        """Build a Telemetry with mocked internals injected directly."""
        # --- span / status mocks ---
        mock_span = MagicMock()
        mock_span.__enter__ = MagicMock(return_value=mock_span)
        mock_span.__exit__ = MagicMock(return_value=False)

        mock_tracer = MagicMock()
        mock_tracer.start_as_current_span.return_value = mock_span

        mock_cost_counter = MagicMock()
        mock_error_counter = MagicMock()

        # Use a disabled Telemetry as scaffold, then inject mocks
        t = Telemetry(TelemetryConfig(enabled=False))
        # Override internal state to simulate a fully initialised enabled instance
        object.__setattr__(t, "_enabled", True)  # Telemetry is not frozen, but use setattr
        t._enabled = True
        t._tracer = mock_tracer
        t._cost_counter = mock_cost_counter
        t._error_counter = mock_error_counter
        t._otel_available = True

        # Provide simple enum-like objects for SpanKind / StatusCode
        class _SpanKind:
            CLIENT = "CLIENT"
            INTERNAL = "INTERNAL"

        class _StatusCode:
            OK = "OK"
            ERROR = "ERROR"

        t._SpanKind = _SpanKind
        t._StatusCode = _StatusCode

        return t, mock_tracer, mock_span, mock_cost_counter, mock_error_counter

    def test_setup_succeeds_when_packages_present(self) -> None:
        """_setup must not raise when opentelemetry is patched in sys.modules."""
        mock_span = MagicMock()
        mock_span.__enter__ = MagicMock(return_value=mock_span)
        mock_span.__exit__ = MagicMock(return_value=False)

        SpanKind = MagicMock()
        SpanKind.CLIENT = "CLIENT"
        SpanKind.INTERNAL = "INTERNAL"
        StatusCode = MagicMock()
        StatusCode.OK = "OK"
        StatusCode.ERROR = "ERROR"

        mock_trace_mod = MagicMock()
        mock_trace_mod.SpanKind = SpanKind
        mock_trace_mod.StatusCode = StatusCode
        mock_meter = MagicMock()
        mock_meter.create_counter.return_value = MagicMock()
        mock_metrics_mod = MagicMock()
        mock_metrics_mod.get_meter.return_value = mock_meter

        mocks = {
            "opentelemetry": MagicMock(),
            "opentelemetry.trace": mock_trace_mod,
            "opentelemetry.metrics": mock_metrics_mod,
            "opentelemetry.sdk": MagicMock(),
            "opentelemetry.sdk.trace": MagicMock(),
            "opentelemetry.sdk.trace.export": MagicMock(),
            "opentelemetry.sdk.metrics": MagicMock(),
            "opentelemetry.sdk.metrics.export": MagicMock(),
            "opentelemetry.sdk.resources": MagicMock(),
            "opentelemetry.exporter.otlp.proto.http.trace_exporter": MagicMock(),
            "opentelemetry.exporter.otlp.proto.http.metric_exporter": MagicMock(),
        }
        with patch.dict(sys.modules, mocks):
            t = Telemetry(TelemetryConfig(enabled=True))
        assert t._otel_available is True
        assert t._tracer is not None

    def test_trace_turn_calls_start_span(self) -> None:
        t, mock_tracer, mock_span, _, _ = self._make_telemetry_with_mocks()
        t.trace_turn("sess-1", "gpt-4o", 200, 100, 350.0)
        mock_tracer.start_as_current_span.assert_called_once()
        call_args = mock_tracer.start_as_current_span.call_args
        assert call_args[0][0] == "llm.turn"

    def test_trace_turn_sets_attributes(self) -> None:
        t, mock_tracer, mock_span, _, _ = self._make_telemetry_with_mocks()
        t.trace_turn("sess-abc", "claude-3", 500, 200, 800.0)
        mock_span.set_attribute.assert_any_call("session.id", "sess-abc")
        mock_span.set_attribute.assert_any_call("llm.model", "claude-3")
        mock_span.set_attribute.assert_any_call("llm.tokens.input", 500)
        mock_span.set_attribute.assert_any_call("llm.tokens.output", 200)
        mock_span.set_attribute.assert_any_call("llm.duration_ms", 800.0)

    def test_trace_tool_success(self) -> None:
        t, mock_tracer, mock_span, _, _ = self._make_telemetry_with_mocks()
        t.trace_tool("bash", 25.5, is_error=False)
        mock_tracer.start_as_current_span.assert_called_once()
        span_name = mock_tracer.start_as_current_span.call_args[0][0]
        assert span_name == "tool.bash"
        mock_span.set_attribute.assert_any_call("tool.name", "bash")
        mock_span.set_attribute.assert_any_call("tool.is_error", False)

    def test_trace_tool_error_sets_error_status(self) -> None:
        t, mock_tracer, mock_span, _, _ = self._make_telemetry_with_mocks()
        t.trace_tool("bash", 5.0, is_error=True)
        mock_span.set_attribute.assert_any_call("tool.is_error", True)
        mock_span.set_status.assert_called()
        status_call_args = mock_span.set_status.call_args[0][0]
        assert status_call_args == "ERROR"

    def test_trace_tool_ok_status(self) -> None:
        t, _, mock_span, _, _ = self._make_telemetry_with_mocks()
        t.trace_tool("read_file", 3.0, is_error=False)
        mock_span.set_status.assert_called()
        status_call_args = mock_span.set_status.call_args[0][0]
        assert status_call_args == "OK"

    def test_record_cost_adds_counter(self) -> None:
        t, _, _, mock_cost_counter, _ = self._make_telemetry_with_mocks()
        t.record_cost("gpt-4o", 1000, 500, 0.0025)
        mock_cost_counter.add.assert_called_once()
        add_args = mock_cost_counter.add.call_args
        assert add_args[0][0] == pytest.approx(0.0025)
        attrs = add_args[1]["attributes"]
        assert attrs["llm.model"] == "gpt-4o"
        assert attrs["llm.tokens.input"] == 1000
        assert attrs["llm.tokens.output"] == 500

    def test_record_error_increments_counter(self) -> None:
        t, _, _, _, mock_error_counter = self._make_telemetry_with_mocks()
        t.record_error("ProviderError", "HTTP 500")
        mock_error_counter.add.assert_called_once()
        add_args = mock_error_counter.add.call_args
        assert add_args[0][0] == 1
        assert add_args[1]["attributes"]["error.type"] == "ProviderError"

    def test_error_message_truncated_to_256(self) -> None:
        t, _, _, _, mock_error_counter = self._make_telemetry_with_mocks()
        long_msg = "x" * 1000
        t.record_error("E", long_msg)
        attrs = mock_error_counter.add.call_args[1]["attributes"]
        assert len(attrs["error.message"]) == 256


# ---------------------------------------------------------------------------
# Config integration: TelemetryConfig in RuntimeConfig
# ---------------------------------------------------------------------------

class TestRuntimeConfigTelemetry:
    def test_default_telemetry_config(self) -> None:
        from llm_code.runtime.config import RuntimeConfig
        cfg = RuntimeConfig()
        assert cfg.telemetry.enabled is False
        assert cfg.telemetry.endpoint == "http://localhost:4318"
        assert cfg.telemetry.service_name == "llm-code"

    def test_load_config_parses_telemetry(self) -> None:
        from pathlib import Path
        import tempfile
        import json
        from llm_code.runtime.config import load_config

        with tempfile.TemporaryDirectory() as tmpdir:
            cfg_path = Path(tmpdir) / "config.json"
            cfg_path.write_text(json.dumps({
                "telemetry": {
                    "enabled": True,
                    "endpoint": "http://otel-collector:4318",
                    "service_name": "my-agent",
                }
            }))
            rt = load_config(
                user_dir=Path(tmpdir),
                project_dir=Path(tmpdir),
                local_path=cfg_path,
                cli_overrides={},
            )
        assert rt.telemetry.enabled is True
        assert rt.telemetry.endpoint == "http://otel-collector:4318"
        assert rt.telemetry.service_name == "my-agent"
