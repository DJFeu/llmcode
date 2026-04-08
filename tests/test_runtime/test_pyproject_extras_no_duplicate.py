"""Issue 3 fix: pyproject.toml must not contain a duplicate [tracing] extra
alongside [telemetry]. langfuse>=3.0 must live under [telemetry].
"""
from __future__ import annotations

from pathlib import Path


def _load_extras() -> dict:
    try:
        import tomllib  # py311+
    except ImportError:  # pragma: no cover
        import tomli as tomllib  # type: ignore
    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text())
    return data["project"]["optional-dependencies"]


def test_no_duplicate_tracing_extra() -> None:
    extras = _load_extras()
    assert "tracing" not in extras, (
        "Duplicate [tracing] extra should be removed; use [telemetry] only."
    )


def test_telemetry_extra_includes_langfuse() -> None:
    extras = _load_extras()
    assert "telemetry" in extras
    joined = " ".join(extras["telemetry"])
    assert "langfuse" in joined
