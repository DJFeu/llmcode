"""M2 — Graph helper tests (Task 2.4).

Tests for the hand-rolled DAG helper used by `Pipeline`. Kahn's
algorithm for topological sort + Tarjan-style cycle detection.

Spec: docs/superpowers/specs/2026-04-21-llm-code-v12-haystack-borrow-design.md §5.2
Plan: docs/superpowers/plans/2026-04-21-llm-code-v12-pipeline-dag.md Task 2.4
"""
from __future__ import annotations

import pytest


class TestGraphImports:
    def test_graph_module_importable(self) -> None:
        from llm_code.engine import graph as graph_mod

        assert hasattr(graph_mod, "Graph")
        assert hasattr(graph_mod, "CyclicGraphError")

    def test_graph_class_importable(self) -> None:
        from llm_code.engine.graph import Graph

        assert Graph is not None

    def test_cyclic_graph_error_is_runtime_error(self) -> None:
        from llm_code.engine.graph import CyclicGraphError

        assert issubclass(CyclicGraphError, RuntimeError)


class TestGraphBasics:
    def test_empty_graph_topological_sort_returns_empty(self) -> None:
        from llm_code.engine.graph import Graph

        g = Graph()
        assert g.topological_sort() == []

    def test_single_node_no_edges(self) -> None:
        from llm_code.engine.graph import Graph

        g = Graph()
        g.add_node("a")
        assert g.topological_sort() == ["a"]

    def test_add_node_is_idempotent(self) -> None:
        from llm_code.engine.graph import Graph

        g = Graph()
        g.add_node("a")
        g.add_node("a")
        assert g.topological_sort() == ["a"]

    def test_add_edge_requires_known_nodes(self) -> None:
        from llm_code.engine.graph import Graph

        g = Graph()
        g.add_node("a")
        with pytest.raises(KeyError):
            g.add_edge("a", "missing")

    def test_add_edge_requires_known_source(self) -> None:
        from llm_code.engine.graph import Graph

        g = Graph()
        g.add_node("b")
        with pytest.raises(KeyError):
            g.add_edge("missing", "b")


class TestTopologicalSort:
    def test_linear_chain_order(self) -> None:
        from llm_code.engine.graph import Graph

        g = Graph()
        for n in "abcd":
            g.add_node(n)
        g.add_edge("a", "b")
        g.add_edge("b", "c")
        g.add_edge("c", "d")
        assert g.topological_sort() == ["a", "b", "c", "d"]

    def test_diamond_deterministic_order(self) -> None:
        """Diamond: a → b, a → c, b → d, c → d.

        Kahn's algorithm with sorted neighbour iteration yields a
        stable order: a, b, c, d (b before c because alphabetical).
        """
        from llm_code.engine.graph import Graph

        g = Graph()
        for n in "abcd":
            g.add_node(n)
        g.add_edge("a", "b")
        g.add_edge("a", "c")
        g.add_edge("b", "d")
        g.add_edge("c", "d")
        order = g.topological_sort()
        assert order[0] == "a"
        assert order[-1] == "d"
        # b and c both depend only on a → both can be first after a;
        # stable sort means alphabetical: b before c.
        assert order.index("b") < order.index("c")

    def test_disconnected_components_ok(self) -> None:
        """Two independent subgraphs both appear in output."""
        from llm_code.engine.graph import Graph

        g = Graph()
        for n in ["a", "b", "x", "y"]:
            g.add_node(n)
        g.add_edge("a", "b")
        g.add_edge("x", "y")
        order = g.topological_sort()
        assert set(order) == {"a", "b", "x", "y"}
        assert order.index("a") < order.index("b")
        assert order.index("x") < order.index("y")

    def test_parallel_edges_same_source_dest_is_idempotent(self) -> None:
        from llm_code.engine.graph import Graph

        g = Graph()
        g.add_node("a")
        g.add_node("b")
        g.add_edge("a", "b")
        g.add_edge("a", "b")  # duplicate edge should not inflate in-degree
        assert g.topological_sort() == ["a", "b"]

    def test_multiple_roots_sorted(self) -> None:
        from llm_code.engine.graph import Graph

        g = Graph()
        for n in ["x", "a", "m", "z"]:
            g.add_node(n)
        # All isolated → alphabetical order.
        order = g.topological_sort()
        assert order == sorted(order)

    def test_complex_dag_respects_dependencies(self) -> None:
        from llm_code.engine.graph import Graph

        g = Graph()
        for n in "abcdefg":
            g.add_node(n)
        g.add_edge("a", "c")
        g.add_edge("b", "c")
        g.add_edge("c", "d")
        g.add_edge("d", "e")
        g.add_edge("d", "f")
        g.add_edge("e", "g")
        g.add_edge("f", "g")
        order = g.topological_sort()
        # Every edge must point forward.
        for src, dsts in [
            ("a", ["c"]),
            ("b", ["c"]),
            ("c", ["d"]),
            ("d", ["e", "f"]),
            ("e", ["g"]),
            ("f", ["g"]),
        ]:
            for dst in dsts:
                assert order.index(src) < order.index(dst), (
                    f"expected {src} before {dst} in {order}"
                )


class TestCycleDetection:
    def test_simple_cycle_detected(self) -> None:
        from llm_code.engine.graph import CyclicGraphError, Graph

        g = Graph()
        g.add_node("a")
        g.add_node("b")
        g.add_edge("a", "b")
        g.add_edge("b", "a")
        with pytest.raises(CyclicGraphError):
            g.topological_sort()

    def test_self_loop_detected(self) -> None:
        from llm_code.engine.graph import CyclicGraphError, Graph

        g = Graph()
        g.add_node("a")
        g.add_edge("a", "a")
        with pytest.raises(CyclicGraphError):
            g.topological_sort()

    def test_three_node_cycle_detected(self) -> None:
        from llm_code.engine.graph import CyclicGraphError, Graph

        g = Graph()
        for n in "abc":
            g.add_node(n)
        g.add_edge("a", "b")
        g.add_edge("b", "c")
        g.add_edge("c", "a")
        with pytest.raises(CyclicGraphError):
            g.topological_sort()

    def test_cycle_error_mentions_involved_nodes(self) -> None:
        from llm_code.engine.graph import CyclicGraphError, Graph

        g = Graph()
        for n in "abc":
            g.add_node(n)
        g.add_edge("a", "b")
        g.add_edge("b", "c")
        g.add_edge("c", "a")
        with pytest.raises(CyclicGraphError) as exc:
            g.topological_sort()
        msg = str(exc.value)
        # At least one of the cyclic nodes shows up in the error.
        assert any(n in msg for n in ("a", "b", "c"))

    def test_dag_with_unrelated_cycle_still_fails(self) -> None:
        """Cycle in one subgraph fails the whole sort."""
        from llm_code.engine.graph import CyclicGraphError, Graph

        g = Graph()
        for n in ["p", "q", "a", "b"]:
            g.add_node(n)
        g.add_edge("p", "q")  # clean subgraph
        g.add_edge("a", "b")
        g.add_edge("b", "a")  # cycle
        with pytest.raises(CyclicGraphError):
            g.topological_sort()

    def test_detect_cycles_returns_empty_for_dag(self) -> None:
        from llm_code.engine.graph import Graph

        g = Graph()
        for n in "abc":
            g.add_node(n)
        g.add_edge("a", "b")
        g.add_edge("b", "c")
        assert g.detect_cycles() == []

    def test_detect_cycles_finds_cycle_nodes(self) -> None:
        from llm_code.engine.graph import Graph

        g = Graph()
        for n in "abc":
            g.add_node(n)
        g.add_edge("a", "b")
        g.add_edge("b", "c")
        g.add_edge("c", "a")
        cycles = g.detect_cycles()
        assert cycles, "expected at least one cycle"
        all_nodes_in_cycles = {n for cycle in cycles for n in cycle}
        assert {"a", "b", "c"}.issubset(all_nodes_in_cycles)
