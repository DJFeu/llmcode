"""Tests for exposing tool capabilities on StreamToolExecStart (H10 deep wire).

Adds a ``_tool_capability_labels(tool)`` helper and threads its output
onto the ``StreamToolExecStart`` event. Non-breaking — the field has
an empty-tuple default, so every existing call site still works.
"""
from __future__ import annotations

from llm_code.api.types import StreamToolExecStart
from llm_code.runtime.tool_pipeline import _tool_capability_labels


# ---------- Event shape ----------


class TestStreamToolExecStartCapabilitiesField:
    def test_default_tool_capabilities_is_empty(self) -> None:
        evt = StreamToolExecStart(tool_name="bash", args_summary="echo", tool_id="c1")
        assert evt.tool_capabilities == ()

    def test_custom_capabilities_preserved(self) -> None:
        evt = StreamToolExecStart(
            tool_name="bash", args_summary="echo", tool_id="c1",
            tool_capabilities=("read_only", "network"),
        )
        assert evt.tool_capabilities == ("read_only", "network")


# ---------- _tool_capability_labels helper ----------


class _ReadTool:
    def is_read_only(self, args: dict) -> bool:
        return True


class _DestructiveTool:
    def is_destructive(self, args: dict) -> bool:
        return True


class _RollbackableTool:
    def is_destructive(self, args: dict) -> bool:
        return True

    def get_rollback_operations(self) -> list[dict]:
        return [{"op": "restore"}]


class _NetworkTool:
    def makes_network_call(self, args: dict) -> bool:
        return True


class _BareTool:
    """Satisfies no Protocol."""


class TestCapabilityLabels:
    def test_read_only(self) -> None:
        labels = _tool_capability_labels(_ReadTool())
        assert "read_only" in labels

    def test_destructive(self) -> None:
        labels = _tool_capability_labels(_DestructiveTool())
        assert "destructive" in labels

    def test_rollbackable_implies_destructive(self) -> None:
        labels = _tool_capability_labels(_RollbackableTool())
        assert set(labels) >= {"destructive", "rollbackable"}

    def test_network(self) -> None:
        labels = _tool_capability_labels(_NetworkTool())
        assert "network" in labels

    def test_bare_tool_has_no_labels(self) -> None:
        assert _tool_capability_labels(_BareTool()) == ()

    def test_labels_sorted_for_stable_output(self) -> None:
        """Sorted order keeps logs / snapshots deterministic across runs."""
        labels = _tool_capability_labels(_RollbackableTool())
        assert list(labels) == sorted(labels)

    def test_returns_tuple(self) -> None:
        """Callers read the field from a frozen dataclass — must be
        a hashable immutable sequence."""
        assert isinstance(_tool_capability_labels(_ReadTool()), tuple)
