"""v13 Phase B parity gate — prompt routing.

Reads the pre-Phase-B snapshot captured by ``scripts/capture_prompt_baseline.py``
and asserts the new profile-driven path
(``load_intro_prompt(resolve_profile_for_model(mid))``) produces a
byte-identical string for every recorded model id.

This test is intentionally tied to the snapshot file. Phase C deletes
both this directory and the JSON fixture once mainline tests exercise
the same path. The parity test is the contractual gate for cutting
Phase B → v2.3.0.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from llm_code.runtime.prompt import load_intro_prompt
from llm_code.runtime.profile_registry import (
    _ensure_builtin_profiles_loaded,
    resolve_profile_for_model,
)

_FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "tests"
    / "fixtures"
    / "pre_v13_prompt_snapshot.json"
)


def _load_snapshot() -> dict[str, str]:
    if not _FIXTURE.is_file():
        pytest.skip(
            "pre_v13_prompt_snapshot.json missing — run "
            "scripts/capture_prompt_baseline.py first."
        )
    return json.loads(_FIXTURE.read_text())


_SNAPSHOT = _load_snapshot()


@pytest.fixture(scope="module", autouse=True)
def _populate_registry() -> None:
    """Load the built-in TOMLs once per test module.

    Idempotent — ``_ensure_builtin_profiles_loaded`` short-circuits
    after the first call. We don't reset_registry here because the
    parity assertion explicitly tests the production resolution
    behaviour seen by user code.
    """
    _ensure_builtin_profiles_loaded()


@pytest.mark.parametrize("model_id", sorted(_SNAPSHOT.keys()))
def test_prompt_byte_identical_to_pre_v13(model_id: str) -> None:
    """Every model id present in the pre-v13 snapshot must produce the
    exact same intro prompt under the new profile-driven path."""
    expected = _SNAPSHOT[model_id]
    profile = resolve_profile_for_model(model_id)
    actual = load_intro_prompt(profile)
    assert actual == expected, (
        f"prompt drift for model_id={model_id!r}\n"
        f"  expected[:120] = {expected[:120]!r}\n"
        f"  actual[:120]   = {actual[:120]!r}"
    )
