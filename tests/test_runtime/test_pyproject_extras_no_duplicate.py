"""Issue 3 fix: pyproject.toml must not contain a duplicate [tracing] extra
alongside [telemetry]. langfuse>=3.0 must live under [telemetry].

v12 extras refinement: the canonical name is [observability]; [telemetry]
stays as a backwards-compatible alias. Both must carry langfuse.
"""
from __future__ import annotations

from pathlib import Path


def _load_pyproject() -> dict:
    try:
        import tomllib  # py311+
    except ImportError:  # pragma: no cover
        import tomli as tomllib  # type: ignore
    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    return tomllib.loads(pyproject.read_text())


def _load_extras() -> dict:
    return _load_pyproject()["project"]["optional-dependencies"]


def test_no_duplicate_tracing_extra() -> None:
    extras = _load_extras()
    assert "tracing" not in extras, (
        "Duplicate [tracing] extra should be removed; use [observability] "
        "(or the [telemetry] alias)."
    )


def test_telemetry_extra_includes_langfuse() -> None:
    extras = _load_extras()
    assert "telemetry" in extras
    joined = " ".join(extras["telemetry"])
    assert "langfuse" in joined


def test_observability_extra_exists() -> None:
    extras = _load_extras()
    assert "observability" in extras, (
        "[observability] is the canonical extra for the OTLP/Langfuse/"
        "Prometheus stack."
    )


def test_observability_extra_contains_exporter_langfuse_prometheus() -> None:
    extras = _load_extras()
    joined = " ".join(extras["observability"])
    assert "opentelemetry-exporter-otlp" in joined
    assert "langfuse" in joined
    assert "prometheus-client" in joined


def test_otel_api_and_sdk_in_core() -> None:
    """M6 design: core tracing is OTel-first — the API + SDK must ship in
    core dependencies so every install can emit spans, even without the
    optional exporter extras."""
    data = _load_pyproject()
    core = " ".join(data["project"]["dependencies"])
    assert "opentelemetry-api" in core
    assert "opentelemetry-sdk" in core


def test_migrate_extra_exists() -> None:
    extras = _load_extras()
    assert "migrate" in extras
    joined = " ".join(extras["migrate"])
    assert "libcst" in joined
    assert "tomlkit" in joined


def test_all_extra_bundles_everything() -> None:
    extras = _load_extras()
    assert "all" in extras
    joined = " ".join(extras["all"])
    # Observability
    assert "opentelemetry-exporter-otlp" in joined
    assert "langfuse" in joined
    assert "prometheus-client" in joined
    # Hayhooks
    assert "fastapi" in joined
    assert "uvicorn" in joined
    # Memory
    assert "sentence-transformers" in joined
    # Memory-rerank
    assert "onnxruntime" in joined
    # Migrate
    assert "libcst" in joined
    assert "tomlkit" in joined
