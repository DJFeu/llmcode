"""Pipeline — DAG of Components.

Borrowed shape: ``haystack/core/pipeline/pipeline.py``. Not borrowed:

- Component serialisation (``to_dict`` / ``from_dict``) and YAML export.
- Warm-up / shutdown hooks.
- Async execution — lands in M5 (``async_pipeline.py``).

Public surface:

- :class:`Pipeline` — add Components, connect their sockets, run the DAG.
- :class:`SocketMismatchError` — raised by :meth:`Pipeline.connect` when
  two sockets have incompatible declared types.

Socket resolution rules (``_types_compatible``):

1. ``src is dst`` or either is :data:`typing.Any` → compatible.
2. ``issubclass(src, dst)`` → compatible (covariant narrowing allowed).
3. ``TypeError`` from ``issubclass`` (e.g. ``list[int]``) → compatible.
   Typing generics are deferred to run time; we do not attempt
   parametric subtyping in v12 M2.

Spec: docs/superpowers/specs/2026-04-21-llm-code-v12-haystack-borrow-design.md §5.2, §6.1
Plan: docs/superpowers/plans/2026-04-21-llm-code-v12-pipeline-dag.md Task 2.3 + 2.5
"""
from __future__ import annotations

import logging
from typing import Any

from llm_code.engine.component import (
    Socket,
    get_input_sockets,
    get_output_sockets,
    is_component,
)
from llm_code.engine.graph import CyclicGraphError, Graph

logger = logging.getLogger(__name__)


class SocketMismatchError(ValueError):
    """Raised when two sockets cannot be connected due to type mismatch."""


class Pipeline:
    """Directed acyclic graph of Components.

    Lifecycle:

    1. Construct the Pipeline.
    2. :meth:`add_component` one Component instance per logical stage.
    3. :meth:`connect` output sockets to downstream input sockets.
    4. Optionally :meth:`validate` to surface cycles / state-write
       conflicts before the first run.
    5. :meth:`run` to execute end-to-end; inputs keyed by component name.
    """

    def __init__(self) -> None:
        self._graph = Graph()
        self._components: dict[str, Any] = {}
        # Each connection is (src_name, src_socket, dst_name, dst_socket).
        self._connections: list[tuple[str, str, str, str]] = []

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def add_component(self, name: str, instance: Any) -> None:
        """Register a Component instance under ``name``.

        Raises:
            ValueError: if ``name`` is already registered.
            TypeError: if ``instance`` is not decorated with ``@component``.
        """
        if name in self._components:
            raise ValueError(f"component name {name!r} already registered")
        if not is_component(instance):
            raise TypeError(
                f"{type(instance).__name__} is not a @component — "
                f"decorate the class with @component before wiring."
            )
        self._components[name] = instance
        self._graph.add_node(name)

    def connect(self, sender: str, receiver: str) -> None:
        """Wire an output socket to a downstream input socket.

        Both arguments are ``"component.socket"`` strings. Types are
        checked via :func:`_types_compatible`; incompatible connections
        raise :class:`SocketMismatchError` at build time.

        Raises:
            ValueError: if either argument is missing the dot separator.
            KeyError: if either component is unknown, or the named
                socket does not exist on the resolved component.
            SocketMismatchError: if declared types cannot be connected.
        """
        src_name, src_sock = self._parse(sender)
        dst_name, dst_sock = self._parse(receiver)
        if src_name not in self._components:
            raise KeyError(f"unknown component in sender: {src_name!r}")
        if dst_name not in self._components:
            raise KeyError(f"unknown component in receiver: {dst_name!r}")

        src_outputs = get_output_sockets(type(self._components[src_name]))
        dst_inputs = get_input_sockets(type(self._components[dst_name]))
        if src_sock not in src_outputs:
            raise KeyError(
                f"{src_name!r} has no output socket {src_sock!r} "
                f"(available: {sorted(src_outputs)})"
            )
        if dst_sock not in dst_inputs:
            raise KeyError(
                f"{dst_name!r} has no input socket {dst_sock!r} "
                f"(available: {sorted(dst_inputs)})"
            )
        src_socket = src_outputs[src_sock]
        dst_socket = dst_inputs[dst_sock]
        if not self._types_compatible(src_socket.type, dst_socket.type):
            raise SocketMismatchError(
                f"{sender} ({src_socket.type}) → {receiver} ({dst_socket.type})"
            )
        self._graph.add_edge(src_name, dst_name)
        self._connections.append((src_name, src_sock, dst_name, dst_sock))

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def run(
        self, inputs: dict[str, dict[str, Any]]
    ) -> dict[str, dict[str, Any]]:
        """Execute the Pipeline in topological order.

        Args:
            inputs: Entry inputs, keyed by component name. Each value
                is a dict of socket name → value supplied directly to
                that component (useful for entry-point sockets that
                aren't fed by an upstream component).

        Returns:
            Per-component output dicts. The outer dict is keyed by the
            component name passed to :meth:`add_component`; the inner
            dict is whatever the component's ``run()`` returned.

        Raises:
            RuntimeError: wrapping a :class:`CyclicGraphError` if the
                pipeline contains a cycle.

        **M6 observability hook:** the whole run opens a ``pipeline_span``
        with the pipeline's class name. Individual component spans are
        opened inside ``@component`` via :func:`traced_component`, so the
        tree is ``pipeline.<name>`` > ``component.<Comp>`` per component.
        """
        try:
            order = self._graph.topological_sort()
        except CyclicGraphError as exc:
            raise RuntimeError(f"pipeline has a cycle: {exc}") from exc

        # Observability: open a pipeline-scoped span. Import is guarded
        # so engine import never fails even on a broken observability
        # install — the context manager is a no-op when OTel is missing.
        try:
            from llm_code.engine.tracing import pipeline_span as _pipeline_span
        except Exception:  # pragma: no cover - defensive
            _pipeline_span = None

        if _pipeline_span is None:
            return self._run_inner(order, inputs)
        with _pipeline_span(type(self).__name__):
            return self._run_inner(order, inputs)

    def _run_inner(
        self,
        order: list[str],
        inputs: dict[str, dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        """Topological execution body; factored out so the pipeline
        span wrapper can call it inside a context manager."""
        outputs: dict[str, dict[str, Any]] = {}
        for comp_name in order:
            comp_inputs = self._resolve_inputs(comp_name, inputs, outputs)
            try:
                result = self._components[comp_name].run(**comp_inputs)
            except Exception:
                logger.exception("component %s failed", comp_name)
                raise
            outputs[comp_name] = result if result is not None else {}
        return outputs

    def _resolve_inputs(
        self,
        comp_name: str,
        entry_inputs: dict[str, dict[str, Any]],
        outputs: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        """Collect inputs for ``comp_name`` from entry inputs + upstream outputs.

        Entry inputs take precedence for entry-point sockets; upstream
        values win when the two would overlap on an internally-wired
        socket. The ordering below writes entry values first then
        overlays upstream results.
        """
        resolved: dict[str, Any] = dict(entry_inputs.get(comp_name, {}))
        for (src_name, src_sock, dst_name, dst_sock) in self._connections:
            if dst_name == comp_name:
                resolved[dst_sock] = outputs[src_name][src_sock]
        return resolved

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate(self) -> None:
        """Eagerly check for cycles and conflicting state-write decls.

        Does not guard against missing required inputs — those depend
        on what the caller hands to :meth:`run` and therefore only
        show up at run time.

        Raises:
            RuntimeError: if the Pipeline contains a cycle.
            ValueError: if two Components declare
                ``@state_writes`` on the same key.
        """
        try:
            self._graph.topological_sort()
        except CyclicGraphError as exc:
            raise RuntimeError(f"pipeline has a cycle: {exc}") from exc
        self._check_state_write_conflicts()

    def _check_state_write_conflicts(self) -> None:
        """Ensure no two Components declare writes on the same State key."""
        # key → list of component names that write it.
        writers: dict[str, list[str]] = {}
        for name, inst in self._components.items():
            keys = getattr(inst, "__state_writes__", frozenset())
            for k in keys:
                writers.setdefault(k, []).append(name)
        conflicts = {k: names for k, names in writers.items() if len(names) > 1}
        if conflicts:
            detail = "; ".join(
                f"key {k!r} written by {sorted(names)}"
                for k, names in sorted(conflicts.items())
            )
            raise ValueError(f"state_writes conflict: {detail}")

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def inputs(self) -> dict[str, dict[str, Socket]]:
        """Return entry-point sockets — those not fed by any connection.

        The outer key is the component name; the inner key is the
        socket name; the value is the :class:`Socket` descriptor. An
        entry-point socket is one for which no ``connect(..., comp.name)``
        call has been made.
        """
        fed: set[tuple[str, str]] = {
            (dst_name, dst_sock) for (_s, _ss, dst_name, dst_sock) in self._connections
        }
        result: dict[str, dict[str, Socket]] = {}
        for comp_name, inst in self._components.items():
            sockets = get_input_sockets(type(inst))
            open_sockets = {
                name: sock
                for name, sock in sockets.items()
                if (comp_name, name) not in fed
            }
            if open_sockets:
                result[comp_name] = open_sockets
        return result

    def outputs(self) -> dict[str, dict[str, Socket]]:
        """Return exit-point sockets — those not consumed by any connection."""
        consumed: set[tuple[str, str]] = {
            (src_name, src_sock) for (src_name, src_sock, _d, _ds) in self._connections
        }
        result: dict[str, dict[str, Socket]] = {}
        for comp_name, inst in self._components.items():
            sockets = get_output_sockets(type(inst))
            open_sockets = {
                name: sock
                for name, sock in sockets.items()
                if (comp_name, name) not in consumed
            }
            if open_sockets:
                result[comp_name] = open_sockets
        return result

    def to_dot(self) -> str:
        """Return a Graphviz DOT representation — useful for debugging.

        Nodes are quoted to tolerate component names that contain
        special characters. Edges include the socket pair as a label
        so a maintainer reading the graph can see which sockets are
        wired together without cross-referencing the source.
        """
        lines: list[str] = ["digraph Pipeline {"]
        for name in sorted(self._components.keys()):
            lines.append(f'    "{name}";')
        for src_name, src_sock, dst_name, dst_sock in self._connections:
            label = f"{src_sock} → {dst_sock}"
            lines.append(f'    "{src_name}" -> "{dst_name}" [label="{label}"];')
        lines.append("}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse(s: str) -> tuple[str, str]:
        """Split ``"component.socket"`` into its parts.

        We ``rsplit`` on the last dot so component names are allowed to
        contain dots (e.g. namespaced names). The socket segment is
        the final token.
        """
        if "." not in s:
            raise ValueError(f"expected 'component.socket', got {s!r}")
        name, sock = s.rsplit(".", 1)
        return name, sock

    @staticmethod
    def _types_compatible(src: type, dst: type) -> bool:
        """Loose static compatibility check for Socket types.

        Accepts when either side is :data:`typing.Any`, when the source
        is a subclass of the destination, or when the check can't be
        done at all (typing generics → deferred to run time).
        """
        if src is dst:
            return True
        if src is Any or dst is Any:
            return True
        try:
            if isinstance(src, type) and isinstance(dst, type):
                return issubclass(src, dst)
        except TypeError:
            return True
        # Non-class annotation (e.g. `list[int]`) — defer to run time.
        return True
