"""Lightweight directed graph for Pipeline DAG wiring.

This module is deliberately hand-rolled to avoid a runtime dependency
on `networkx`. Scope is intentionally narrow:

- `Graph.add_node(name)` — idempotent node registration.
- `Graph.add_edge(src, dst)` — requires both endpoints to exist.
  Duplicate edges are deduplicated so in-degree math remains correct
  when callers accidentally `connect()` the same pair twice.
- `Graph.topological_sort()` — Kahn's algorithm with alphabetically
  stable neighbour iteration so pipeline run order is deterministic
  across Python versions.
- `Graph.detect_cycles()` — diagnostic helper (simple SCC-style DFS)
  used for human-readable error messages; not in the hot path.

Design decision (Task 2.1, plan §2026-04-21-llm-code-v12-pipeline-dag.md):
hand-rolled beats `networkx` for this scope because (a) <100 LOC,
(b) no new runtime dep, (c) full control over the ordering guarantees
`Pipeline` exposes in its docstring. Escalate to `networkx` only if
Pipeline grows past ~30 component types or we need SCC analysis in
production code.

Spec: docs/superpowers/specs/2026-04-21-llm-code-v12-haystack-borrow-design.md §5.2
Plan: docs/superpowers/plans/2026-04-21-llm-code-v12-pipeline-dag.md Task 2.4
"""
from __future__ import annotations

from collections import defaultdict, deque


class CyclicGraphError(RuntimeError):
    """Raised by `Graph.topological_sort` when the graph contains a cycle.

    Error message lists the nodes that remain unresolved after Kahn's
    algorithm completes; those are guaranteed to participate in at
    least one cycle (all have non-zero in-degree after processing).
    """


class Graph:
    """Minimal directed graph over string node identifiers.

    Nodes are keyed by `str` (component names in Pipeline usage).
    Edges are stored in both adjacency and reverse-adjacency maps for
    O(1) in-degree lookup during Kahn's sort.
    """

    def __init__(self) -> None:
        self._nodes: set[str] = set()
        self._adjacency: dict[str, set[str]] = defaultdict(set)
        self._reverse: dict[str, set[str]] = defaultdict(set)

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def add_node(self, name: str) -> None:
        """Register a node. Idempotent — re-adding is a no-op."""
        self._nodes.add(name)

    def add_edge(self, src: str, dst: str) -> None:
        """Add a directed edge ``src → dst``.

        Raises:
            KeyError: if either endpoint has not been registered via
                :meth:`add_node`. This catches typos at pipeline build
                time rather than deferring to run time.
        """
        if src not in self._nodes:
            raise KeyError(f"unknown node: {src!r}")
        if dst not in self._nodes:
            raise KeyError(f"unknown node: {dst!r}")
        # `set.add` deduplicates; a repeated edge does not inflate the
        # in-degree.
        self._adjacency[src].add(dst)
        self._reverse[dst].add(src)

    # ------------------------------------------------------------------
    # Sort + cycle detection
    # ------------------------------------------------------------------

    def topological_sort(self) -> list[str]:
        """Return nodes in dependency order using Kahn's algorithm.

        Neighbours are processed in alphabetical order so that the
        produced ordering is stable across runs. Multiple nodes with
        zero in-degree are also drained alphabetically.

        Raises:
            CyclicGraphError: if the graph contains at least one cycle.
        """
        in_degree: dict[str, int] = {n: len(self._reverse[n]) for n in self._nodes}
        # Sort initial zero in-degree nodes so draining order is stable.
        queue: deque[str] = deque(sorted(n for n, d in in_degree.items() if d == 0))
        order: list[str] = []
        while queue:
            n = queue.popleft()
            order.append(n)
            # Alphabetical iteration keeps sibling ordering stable.
            for m in sorted(self._adjacency[n]):
                in_degree[m] -= 1
                if in_degree[m] == 0:
                    queue.append(m)
        if len(order) != len(self._nodes):
            missing = sorted(self._nodes - set(order))
            raise CyclicGraphError(f"cycle involves nodes: {missing}")
        return order

    def detect_cycles(self) -> list[list[str]]:
        """Return cycles found via DFS (diagnostic helper).

        Returns an empty list for DAGs. For cyclic graphs, returns one
        list per simple cycle discovered during DFS traversal. The
        primary runtime use is producing better error messages; tests
        also exercise this for graph correctness.
        """
        cycles: list[list[str]] = []
        visited: set[str] = set()
        stack: list[str] = []
        on_stack: set[str] = set()

        def _dfs(node: str) -> None:
            visited.add(node)
            stack.append(node)
            on_stack.add(node)
            for nxt in sorted(self._adjacency[node]):
                if nxt not in visited:
                    _dfs(nxt)
                elif nxt in on_stack:
                    # Back-edge → cycle. Slice stack from the first
                    # occurrence of `nxt` to form the cycle path.
                    idx = stack.index(nxt)
                    cycles.append(stack[idx:] + [nxt])
            stack.pop()
            on_stack.discard(node)

        for node in sorted(self._nodes):
            if node not in visited:
                _dfs(node)
        return cycles
