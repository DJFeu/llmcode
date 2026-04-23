"""SpeculativeExecutorComponent — v11 tool-result cache as a Pipeline stage.

The **v11-spec'd behaviour** lives here: when the same tool call is
issued twice with equal arguments, the second hit returns the cached
:class:`ToolResult` without paying the execution cost again. This is
distinct from :class:`llm_code.runtime.speculative.SpeculativeExecutor`
which pre-runs a tool inside an OverlayFS before user confirmation —
that feature continues to operate orthogonally. We do not reuse the
same name at import time to keep the legacy executor available under
its original module.

Cache identity
--------------
``sha256(json.dumps({"t": tool_name, "a": tool_args}, sort_keys=True))``

- ``sort_keys=True`` makes the key independent of Python dict insertion
  order.
- ``default=str`` keeps the hash computable for nested values that are
  not JSON-native (e.g. pathlib.Path). Any non-serialisable payload
  raises :class:`TypeError`, which we wrap so the caller sees the same
  ``ValueError`` vocabulary the other v12 Components use.

Semantics
---------
- ``proceed=False`` from an upstream gate is always honoured; the
  Component does not pretend to serve from cache when the call has
  already been denied.
- On cache hit, ``proceed`` is flipped to ``False`` so downstream
  :class:`ToolExecutorComponent` skips re-running the tool. ``cached_result``
  is the stored :class:`ToolResult`.
- On miss, ``proceed`` passes through unchanged and ``cached_result``
  is ``None``.

Eviction
--------
LRU with a default cap of 128 entries; ``max_size`` is constructor
configurable. ``hits`` / ``misses`` counters feed :meth:`stats` for
observability spans in M6.

Plan: docs/superpowers/plans/2026-04-21-llm-code-v12-pipeline-dag.md Task 2.7 Step 3
"""
from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from typing import Any

from llm_code.engine.component import component, output_types
from llm_code.tools.base import ToolResult


_DEFAULT_MAX_SIZE = 128


def _cache_key(tool_name: str, tool_args: dict) -> str:
    """Return a stable sha256 hex digest for ``(tool_name, tool_args)``.

    Nested dicts / lists are allowed as long as every leaf value is
    JSON-encodable under ``default=str``. This covers the common bash /
    file-tool payloads; tools passing exotic objects see ``str(obj)``
    stringification — still stable for identical objects within a
    session, but not portable across processes.
    """
    try:
        payload = json.dumps(
            {"t": tool_name, "a": tool_args},
            sort_keys=True,
            default=str,
        )
    except TypeError as exc:  # pragma: no cover - defensive
        raise ValueError(f"tool_args not JSON-encodable: {exc}") from exc
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@component
@output_types(cache_hit=bool, cached_result=object, proceed=bool)
class SpeculativeExecutorComponent:
    """Cache :class:`ToolResult` by ``(tool_name, sha256(args))``.

    Args:
        max_size: Max LRU entries to retain. Oldest-used entries are
            evicted when the cache fills up. Construction with
            ``max_size=0`` disables caching entirely (useful for parity
            baselines).
    """

    def __init__(self, max_size: int = _DEFAULT_MAX_SIZE) -> None:
        if max_size < 0:
            raise ValueError(f"max_size must be non-negative, got {max_size}")
        self._cache: OrderedDict[str, ToolResult] = OrderedDict()
        self._max_size = max_size
        self._hits = 0
        self._misses = 0

    # ------------------------------------------------------------------
    # Public surface
    # ------------------------------------------------------------------

    def store(self, tool_name: str, tool_args: dict, result: ToolResult) -> None:
        """Insert ``result`` under the key for ``(tool_name, tool_args)``."""
        if self._max_size == 0:
            return
        key = _cache_key(tool_name, tool_args)
        if key in self._cache:
            self._cache.move_to_end(key)
        self._cache[key] = result
        while len(self._cache) > self._max_size:
            self._cache.popitem(last=False)

    def clear(self) -> None:
        self._cache.clear()
        self._hits = 0
        self._misses = 0

    def stats(self) -> dict[str, int]:
        """Return ``{"hits", "misses", "size"}`` — for observability."""
        return {
            "hits": self._hits,
            "misses": self._misses,
            "size": len(self._cache),
        }

    # ------------------------------------------------------------------
    # Component interface
    # ------------------------------------------------------------------

    def run(
        self,
        proceed: bool,
        tool_name: str,
        tool_args: dict,
    ) -> dict[str, Any]:
        """Look up the cache and report hit / miss."""
        if not proceed:
            # Upstream already denied — don't pretend to serve from cache.
            return {"cache_hit": False, "cached_result": None, "proceed": False}

        key = _cache_key(tool_name, tool_args)
        cached = self._cache.get(key)
        if cached is not None:
            self._cache.move_to_end(key)  # LRU touch
            self._hits += 1
            # Short-circuit downstream executor.
            return {
                "cache_hit": True,
                "cached_result": cached,
                "proceed": False,
            }

        self._misses += 1
        return {"cache_hit": False, "cached_result": None, "proceed": True}
