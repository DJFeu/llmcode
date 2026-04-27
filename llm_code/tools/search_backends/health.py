"""Backend health tracking + smart-fallback ordering (v2.8.0 M4).

Tracks per-process health for each search backend so the auto-fallback
chain in :func:`~llm_code.tools.web_search.WebSearchTool._search_with_fallback`
can demote unhealthy backends to the end of the chain instead of
hammering them on every call.

Circuit-breaker logic
---------------------

* 3 consecutive failures (rate-limit / timeout / generic error) opens
  the circuit for 5 minutes (``_CIRCUIT_OPEN_DURATION_S``).
* While the circuit is open, :func:`is_healthy` returns ``False`` and
  :func:`sort_chain` moves the backend to the end of the chain
  (preserving relative order with other unhealthy backends).
* Any successful call resets the failure counter and clears the
  circuit immediately. After the open window passes the circuit
  auto-closes on the next ``is_healthy`` check — no explicit reset.

Concurrency
-----------

The module-level ``_health`` dict is guarded by a ``threading.Lock``
so concurrent ``record_failure`` / ``record_success`` calls from
different threads (e.g. ``asyncio.gather`` over the M5 research
pipeline) don't race on the counters.

Tests
-----

Per-process state means CI test ordering matters. Tests use the
``_reset_for_tests()`` helper from an autouse fixture in
``tests/test_tools/test_search_backends/test_health.py`` to start
from a clean slate.

Plan: docs/superpowers/plans/2026-04-27-llm-code-v17-m4-backend-health.md
Spec: docs/superpowers/specs/2026-04-27-llm-code-v17-rag-pipeline-design.md §3.4
"""
from __future__ import annotations

import dataclasses
import logging
import threading
import time
from typing import Literal

logger = logging.getLogger(__name__)

# Circuit opens after this many consecutive failures.
_FAILURE_THRESHOLD = 3
# Circuit stays open for this many seconds (5 minutes per spec §3.4).
_CIRCUIT_OPEN_DURATION_S = 300.0


FailureKind = Literal["rate_limit", "timeout", "error"]


@dataclasses.dataclass
class BackendHealth:
    """Per-backend health snapshot.

    Mutable on purpose — the dict-of-records pattern keeps mutation
    contained behind the module-level lock.
    """

    last_429_at: float = 0.0
    consecutive_failures: int = 0
    last_success_at: float = 0.0
    circuit_open_until: float = 0.0


# Module-level state. Guarded by ``_lock`` for all mutations.
_health: dict[str, BackendHealth] = {}
_lock = threading.Lock()


def _now() -> float:
    """Monotonic clock for circuit timing.

    Uses ``time.monotonic`` so tests can patch a single function point
    rather than ``time.time`` which has too many other call sites.
    """
    return time.monotonic()


def _get_or_create(name: str) -> BackendHealth:
    """Return the health record for ``name``, creating it if needed.

    Caller MUST hold ``_lock`` when calling this.
    """
    record = _health.get(name)
    if record is None:
        record = BackendHealth()
        _health[name] = record
    return record


def record_failure(name: str, kind: FailureKind = "error") -> None:
    """Record a failure for backend ``name``.

    After :data:`_FAILURE_THRESHOLD` consecutive failures the circuit
    opens for :data:`_CIRCUIT_OPEN_DURATION_S` seconds; subsequent
    ``is_healthy`` calls return ``False`` until the window passes.
    """
    with _lock:
        record = _get_or_create(name)
        record.consecutive_failures += 1
        if kind == "rate_limit":
            record.last_429_at = _now()
        if record.consecutive_failures >= _FAILURE_THRESHOLD:
            already_open = record.circuit_open_until > _now()
            record.circuit_open_until = _now() + _CIRCUIT_OPEN_DURATION_S
            if not already_open:
                logger.warning(
                    "circuit_open backend=%s reason=%s consecutive_failures=%d",
                    name, kind, record.consecutive_failures,
                )


def record_success(name: str) -> None:
    """Record a successful call for backend ``name``.

    Resets the failure counter and clears any open circuit. Logs an
    informational ``circuit_close`` event when transitioning out of
    open state.
    """
    with _lock:
        record = _get_or_create(name)
        was_open = record.circuit_open_until > _now()
        record.consecutive_failures = 0
        record.circuit_open_until = 0.0
        record.last_success_at = _now()
        if was_open:
            logger.info("circuit_close backend=%s", name)


def is_healthy(name: str) -> bool:
    """Return True if backend ``name`` is healthy (circuit closed).

    Auto-resets when the open window passes — the very next call
    after the window sees a healthy backend again, which gives it
    one shot to recover before the failure tracker reopens the
    circuit on its next bad call.
    """
    with _lock:
        record = _health.get(name)
        if record is None:
            return True
        return record.circuit_open_until <= _now()


def sort_chain(chain: tuple[str, ...]) -> tuple[str, ...]:
    """Stable partition: healthy first, unhealthy moved to end.

    Within each partition the relative order from ``chain`` is
    preserved so a user-configured priority chain still walks in
    priority order — unhealthy backends just retry last.
    """
    healthy: list[str] = []
    unhealthy: list[str] = []
    for name in chain:
        if is_healthy(name):
            healthy.append(name)
        else:
            unhealthy.append(name)
    return tuple(healthy + unhealthy)


def snapshot(name: str) -> BackendHealth | None:
    """Return a copy of the health record for inspection.

    Returns ``None`` if no record exists yet.
    """
    with _lock:
        record = _health.get(name)
        if record is None:
            return None
        return dataclasses.replace(record)


def _reset_for_tests() -> None:
    """Clear all health state. Test-only helper.

    The autouse fixture in ``tests/test_tools/test_search_backends/test_health.py``
    calls this between tests so per-process state doesn't bleed across.
    """
    with _lock:
        _health.clear()
