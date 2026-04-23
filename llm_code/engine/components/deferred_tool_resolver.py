"""DeferredToolResolverComponent — lazy tool-schema lookup as a Pipeline stage.

Implements v11's "deferred tools" behaviour: the :class:`ToolRegistry`
is only queried when the pipeline actually needs the tool object. A
small LRU keeps recently-resolved tools hot so repeated calls to the
same tool don't thrash the registry.

Inputs
------
- ``tool_name``: registered tool identifier.
- ``cache_hit``: upstream speculative-cache hit — when ``True`` the
  downstream executor short-circuits, so resolution is unnecessary.
- ``proceed``: upstream gate (permission / rate-limiter). ``False``
  bypasses resolution entirely.

Outputs
-------
- ``resolved_tool``: the tool instance (or ``None`` on miss).
- ``proceed``: downstream-facing gate. ``True`` iff the tool was
  resolved successfully.
- ``resolution_error``: empty on success; human-readable error text
  on failure. Downstream stages surface this to the user when
  ``proceed`` is ``False``.

Plan: docs/superpowers/plans/2026-04-21-llm-code-v12-pipeline-dag.md Task 2.7 Step 4
"""
from __future__ import annotations

from collections import OrderedDict
from typing import Any, Protocol

from llm_code.engine.component import component, output_types


_DEFAULT_CACHE_SIZE = 64


class _Registry(Protocol):
    """Structural type for the subset of :class:`ToolRegistry` we need.

    We depend on the ``get(name) -> Tool | None`` shape only so tests
    can drop in lightweight fakes without constructing the full
    registry.
    """

    def get(self, name: str) -> Any: ...


@component
@output_types(resolved_tool=object, proceed=bool, resolution_error=str)
class DeferredToolResolverComponent:
    """Resolve a tool by name, with LRU caching.

    Args:
        registry: Object exposing ``get(tool_name) -> Tool | None``.
            In production this is a
            :class:`llm_code.tools.registry.ToolRegistry` instance;
            tests pass a trivial fake.
        cache_size: LRU cap. ``0`` disables caching (every call hits
            the registry). Negative values raise :class:`ValueError`.
    """

    def __init__(
        self,
        registry: _Registry,
        *,
        cache_size: int = _DEFAULT_CACHE_SIZE,
    ) -> None:
        if cache_size < 0:
            raise ValueError(f"cache_size must be non-negative, got {cache_size}")
        self._registry = registry
        self._cache_size = cache_size
        self._cache: OrderedDict[str, Any] = OrderedDict()
        self._hits = 0
        self._misses = 0

    # ------------------------------------------------------------------

    def clear(self) -> None:
        self._cache.clear()
        self._hits = 0
        self._misses = 0

    def stats(self) -> dict[str, int]:
        return {
            "hits": self._hits,
            "misses": self._misses,
            "size": len(self._cache),
        }

    # ------------------------------------------------------------------

    def run(
        self,
        proceed: bool,
        cache_hit: bool,
        tool_name: str,
    ) -> dict[str, Any]:
        """Return the resolved tool, or a descriptive failure."""
        if not proceed:
            # Either the upstream gate denied, or the cache already
            # served the call — nothing to resolve.
            return {
                "resolved_tool": None,
                "proceed": False,
                "resolution_error": "",
            }
        if cache_hit:
            # Same idea as above but phrased with the spec vocabulary:
            # a hit means the result is already in hand.
            return {
                "resolved_tool": None,
                "proceed": False,
                "resolution_error": "",
            }

        cached = self._cache.get(tool_name) if self._cache_size else None
        if cached is not None:
            self._cache.move_to_end(tool_name)
            self._hits += 1
            return {
                "resolved_tool": cached,
                "proceed": True,
                "resolution_error": "",
            }

        self._misses += 1
        tool = self._registry.get(tool_name)
        if tool is None:
            return {
                "resolved_tool": None,
                "proceed": False,
                "resolution_error": f"unknown tool {tool_name!r}",
            }

        if self._cache_size:
            self._cache[tool_name] = tool
            while len(self._cache) > self._cache_size:
                self._cache.popitem(last=False)

        return {
            "resolved_tool": tool,
            "proceed": True,
            "resolution_error": "",
        }
