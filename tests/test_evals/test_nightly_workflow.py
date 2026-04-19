"""Smoke tests for the evals-nightly GitHub workflow (C2d).

The workflow isn't executed in CI here — we only assert that:

    1. the YAML is well-formed,
    2. the expected jobs / trigger shapes exist,
    3. the workflow uses the standard actions we expect (checkout,
       setup-python, upload-artifact) at versions we can tolerate.

This catches trivial mis-edits (missing keys, indentation drift)
without requiring a pinned actionlint binary in the dev environment.
"""
from __future__ import annotations

from pathlib import Path

import pytest

try:
    import yaml  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover - dev-only optional dep
    yaml = None  # type: ignore[assignment]


WORKFLOW_PATH = (
    Path(__file__).resolve().parents[2]
    / ".github"
    / "workflows"
    / "evals-nightly.yml"
)


@pytest.mark.skipif(yaml is None, reason="PyYAML not installed")
class TestNightlyWorkflow:
    def test_file_exists(self) -> None:
        assert WORKFLOW_PATH.is_file(), f"missing workflow at {WORKFLOW_PATH}"

    def test_parses_as_yaml(self) -> None:
        doc = yaml.safe_load(WORKFLOW_PATH.read_text())
        assert isinstance(doc, dict)

    def test_name_is_evals_nightly(self) -> None:
        doc = yaml.safe_load(WORKFLOW_PATH.read_text())
        assert doc["name"] == "evals-nightly"

    def test_has_schedule_and_manual_triggers(self) -> None:
        doc = yaml.safe_load(WORKFLOW_PATH.read_text())
        # PyYAML parses bare `on:` into the Python literal True (the
        # YAML 1.1 quirk); under newer PyYAML it stays "on". Accept
        # both so we're resilient to env drift.
        trigger_key = True if True in doc else "on"
        triggers = doc[trigger_key]
        assert isinstance(triggers, dict)
        assert "schedule" in triggers
        assert "workflow_dispatch" in triggers

    def test_has_evals_job(self) -> None:
        doc = yaml.safe_load(WORKFLOW_PATH.read_text())
        assert "jobs" in doc
        assert "evals" in doc["jobs"]

    def test_evals_job_uses_pinned_actions(self) -> None:
        doc = yaml.safe_load(WORKFLOW_PATH.read_text())
        steps = doc["jobs"]["evals"]["steps"]
        used = {step.get("uses") for step in steps if "uses" in step}
        # Major-version pin is fine, but the three staple actions must be used.
        assert any(u and u.startswith("actions/checkout@v") for u in used)
        assert any(u and u.startswith("actions/setup-python@v") for u in used)
        assert any(u and u.startswith("actions/upload-artifact@v") for u in used)

    def test_live_input_is_boolean(self) -> None:
        doc = yaml.safe_load(WORKFLOW_PATH.read_text())
        trigger_key = True if True in doc else "on"
        inputs = doc[trigger_key]["workflow_dispatch"]["inputs"]
        assert inputs["live"]["type"] == "boolean"

    def test_fail_fast_disabled(self) -> None:
        """Nightly runs must not short-circuit — we want every model /
        python-version combination to report its own result."""
        doc = yaml.safe_load(WORKFLOW_PATH.read_text())
        strategy = doc["jobs"]["evals"].get("strategy", {})
        assert strategy.get("fail-fast") is False
