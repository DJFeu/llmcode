"""Tests for type-specific exponential decay scoring."""
from __future__ import annotations

import math
import time

from llm_code.runtime.memory_decay import (
    DECAY_LAMBDA,
    apply_decay,
    decay_factor,
)
from llm_code.runtime.memory_taxonomy import MemoryType


def test_decay_factor_zero_age_is_one() -> None:
    assert decay_factor(MemoryType.USER, 0) == 1.0


def test_decay_factor_user_half_life_140_days() -> None:
    # lambda=0.005 -> half-life = ln(2)/0.005 ≈ 138.6 days
    val = decay_factor(MemoryType.USER, 138.6)
    assert math.isclose(val, 0.5, rel_tol=0.05)


def test_decay_factor_project_half_life_35_days() -> None:
    # lambda=0.02 -> half-life = ln(2)/0.02 ≈ 34.66 days
    val = decay_factor(MemoryType.PROJECT, 34.66)
    assert math.isclose(val, 0.5, rel_tol=0.05)


def test_decay_factor_reference_never_decays() -> None:
    assert decay_factor(MemoryType.REFERENCE, 1000) == 1.0
    assert decay_factor(MemoryType.REFERENCE, 100000) == 1.0


def test_apply_decay_respects_floor() -> None:
    now = time.time()
    very_old = now - (10_000 * 86400)  # ~27 years
    result = apply_decay(1.0, MemoryType.USER, very_old, now=now, floor=0.1)
    # Decay would be ~exp(-50) ≈ 0; floor must clamp to 0.1
    assert math.isclose(result, 0.1, rel_tol=1e-6)


def test_recent_project_beats_old_project() -> None:
    now = time.time()
    recent = apply_decay(1.0, MemoryType.PROJECT, now - 1 * 86400, now=now)
    old = apply_decay(1.0, MemoryType.PROJECT, now - 60 * 86400, now=now)
    assert recent > old


def test_user_beats_project_at_same_age() -> None:
    now = time.time()
    age = now - 30 * 86400
    user_score = apply_decay(1.0, MemoryType.USER, age, now=now)
    project_score = apply_decay(1.0, MemoryType.PROJECT, age, now=now)
    assert user_score > project_score


def test_lambda_override() -> None:
    # Override beats default for the same type.
    base = decay_factor(MemoryType.PROJECT, 10)
    overridden = decay_factor(MemoryType.PROJECT, 10, lambda_override=0.0)
    assert overridden == 1.0
    assert overridden > base


def test_apply_decay_accepts_iso_string() -> None:
    from datetime import datetime, timezone
    now = time.time()
    iso = datetime.fromtimestamp(now - 86400, tz=timezone.utc).isoformat()
    result = apply_decay(1.0, MemoryType.PROJECT, iso, now=now)
    # ~1 day age, lambda=0.02 -> ~exp(-0.02) ≈ 0.9802
    assert 0.95 < result < 1.0


def test_default_lambda_dict_present() -> None:
    assert DECAY_LAMBDA[MemoryType.REFERENCE] == 0.0
    assert DECAY_LAMBDA[MemoryType.PROJECT] > DECAY_LAMBDA[MemoryType.USER]
