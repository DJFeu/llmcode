"""Tests for Langfuse-related TelemetryConfig fields and parsing."""
from __future__ import annotations


from llm_code.runtime.config import TelemetryConfig as ConfigTelemetryConfig
from llm_code.runtime.telemetry import TelemetryConfig as RuntimeTelemetryConfig


def test_runtime_telemetry_config_has_langfuse_fields() -> None:
    cfg = RuntimeTelemetryConfig(
        enabled=True,
        langfuse_public_key="pk-test",
        langfuse_secret_key="sk-test",
        langfuse_host="https://cloud.langfuse.com",
    )
    assert cfg.langfuse_public_key == "pk-test"
    assert cfg.langfuse_secret_key == "sk-test"
    assert cfg.langfuse_host == "https://cloud.langfuse.com"


def test_config_telemetry_config_has_langfuse_fields() -> None:
    cfg = ConfigTelemetryConfig(
        enabled=True,
        langfuse_public_key="pk-test",
        langfuse_secret_key="sk-test",
    )
    assert cfg.langfuse_public_key == "pk-test"
    assert cfg.langfuse_secret_key == "sk-test"
    # Default host is the public Langfuse cloud endpoint
    assert cfg.langfuse_host == "https://cloud.langfuse.com"


def test_telemetry_config_parser_reads_langfuse_keys_from_dict(monkeypatch) -> None:
    from llm_code.runtime.config import _parse_telemetry_config  # added in Step 3

    parsed = _parse_telemetry_config({
        "enabled": True,
        "langfuse_public_key": "pk-from-yaml",
        "langfuse_secret_key": "sk-from-yaml",
    })
    assert parsed.langfuse_public_key == "pk-from-yaml"
    assert parsed.langfuse_secret_key == "sk-from-yaml"


def test_telemetry_config_parser_falls_back_to_env_vars(monkeypatch) -> None:
    from llm_code.runtime.config import _parse_telemetry_config

    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-from-env")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-from-env")
    monkeypatch.setenv("LANGFUSE_HOST", "https://my-langfuse.example.com")

    parsed = _parse_telemetry_config({"enabled": True})
    assert parsed.langfuse_public_key == "pk-from-env"
    assert parsed.langfuse_secret_key == "sk-from-env"
    assert parsed.langfuse_host == "https://my-langfuse.example.com"


def test_telemetry_config_parser_dict_takes_precedence_over_env(monkeypatch) -> None:
    from llm_code.runtime.config import _parse_telemetry_config

    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-from-env")
    parsed = _parse_telemetry_config({
        "enabled": True,
        "langfuse_public_key": "pk-from-yaml",
    })
    assert parsed.langfuse_public_key == "pk-from-yaml"


def test_telemetry_config_defaults_have_no_langfuse_keys() -> None:
    cfg = RuntimeTelemetryConfig()
    assert cfg.langfuse_public_key == ""
    assert cfg.langfuse_secret_key == ""


def test_telemetry_setup_does_not_crash_when_langfuse_keys_set_and_pkg_missing(monkeypatch) -> None:
    import sys

    monkeypatch.setitem(sys.modules, "langfuse", None)
    monkeypatch.setitem(sys.modules, "langfuse.otel", None)

    from llm_code.runtime.telemetry import Telemetry, TelemetryConfig

    cfg = TelemetryConfig(
        enabled=True,
        langfuse_public_key="pk",
        langfuse_secret_key="sk",
    )
    t = Telemetry(cfg)
    assert t is not None


def test_telemetry_records_langfuse_setup_attempt(monkeypatch) -> None:
    import sys

    class _FakeProcessor:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def shutdown(self) -> None:
            """No-op to satisfy atexit TracerProvider.shutdown()."""

        def force_flush(self, timeout_millis: int = 30_000) -> bool:
            return True

        def on_start(self, span, parent_context=None) -> None:
            pass

        def on_end(self, span) -> None:
            pass

    class _FakeOtelMod:
        LangfuseSpanProcessor = _FakeProcessor

    monkeypatch.setitem(sys.modules, "langfuse", type(sys)("langfuse"))
    monkeypatch.setitem(sys.modules, "langfuse.otel", _FakeOtelMod)

    from llm_code.runtime.telemetry import Telemetry, TelemetryConfig

    cfg = TelemetryConfig(
        enabled=True,
        langfuse_public_key="pk",
        langfuse_secret_key="sk",
    )
    t = Telemetry(cfg)
    assert t is not None
