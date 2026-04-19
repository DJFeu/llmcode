"""Opt-in collection for perf tests (H8b).

Perf tests are timing-sensitive and flaky on shared CI runners, so we
keep them out of the default ``pytest tests/`` run. Set
``LLMCODE_PERF=1`` to collect them — the nightly workflow flips that
flag, and local devs can pin new baselines with:

    LLMCODE_PERF=1 UPDATE_BASELINES=1 pytest tests/perf_tests/
"""
from __future__ import annotations

import os

import pytest


def pytest_collection_modifyitems(config, items):  # noqa: ARG001
    if os.environ.get("LLMCODE_PERF") == "1":
        return
    # ``pytest_collection_modifyitems`` in a sub-dir conftest still
    # sees session-wide items, so filter by path to scope the skip to
    # this directory.
    skip = pytest.mark.skip(
        reason="perf tests disabled (set LLMCODE_PERF=1 to enable)"
    )
    for item in items:
        if "perf_tests" in str(item.fspath):
            item.add_marker(skip)
