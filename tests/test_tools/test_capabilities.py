"""Tests for the tool capability Protocol layer (H10 — Sprint 3).

The existing ``Tool`` base class in ``tools/base.py`` exposes
``is_read_only(args)`` and ``is_destructive(args)`` as overridable
methods — a convention the runtime trusts but cannot statically verify.

H10 introduces ``@runtime_checkable`` :class:`typing.Protocol` classes
that tools can opt into explicitly; the pipeline can then branch on
``has_capability(tool, DestructiveCapability)`` instead of hoping every
subclass remembered to override the method. Keeps the existing
convention intact — Protocols are additive markers.
"""
from __future__ import annotations

import pytest

from llm_code.tools.capabilities import (
    DestructiveCapability,
    NetworkCapability,
    ReadOnlyCapability,
    RollbackableCapability,
    has_capability,
)


# ---------- Fake tools implementing capabilities ----------


class FakeReadTool:
    def is_read_only(self, args: dict) -> bool:
        return True


class FakeWriteTool:
    def is_destructive(self, args: dict) -> bool:
        return args.get("delete", False)


class FakeRollbackableTool:
    def is_destructive(self, args: dict) -> bool:  # noqa: ARG002
        return True

    def get_rollback_operations(self) -> list[dict]:
        return [{"op": "restore", "path": "/x"}]


class FakeNetworkTool:
    def makes_network_call(self, args: dict) -> bool:
        return args.get("url") is not None


class BareTool:
    """Tool that implements none of the capability methods."""


# ---------- runtime_checkable Protocols ----------


class TestReadOnlyCapability:
    def test_identifies_read_tool(self) -> None:
        assert isinstance(FakeReadTool(), ReadOnlyCapability)

    def test_rejects_write_tool(self) -> None:
        assert not isinstance(FakeWriteTool(), ReadOnlyCapability)

    def test_rejects_bare_tool(self) -> None:
        assert not isinstance(BareTool(), ReadOnlyCapability)


class TestDestructiveCapability:
    def test_identifies_write_tool(self) -> None:
        assert isinstance(FakeWriteTool(), DestructiveCapability)

    def test_rejects_read_tool(self) -> None:
        assert not isinstance(FakeReadTool(), DestructiveCapability)


class TestRollbackableCapability:
    def test_identifies_rollbackable_tool(self) -> None:
        assert isinstance(FakeRollbackableTool(), RollbackableCapability)

    def test_rejects_non_rollbackable(self) -> None:
        # FakeWriteTool is destructive but has no rollback op
        assert not isinstance(FakeWriteTool(), RollbackableCapability)


class TestNetworkCapability:
    def test_identifies_network_tool(self) -> None:
        assert isinstance(FakeNetworkTool(), NetworkCapability)

    def test_rejects_local_tool(self) -> None:
        assert not isinstance(FakeReadTool(), NetworkCapability)


# ---------- has_capability helper ----------


class TestHasCapabilityHelper:
    def test_delegates_to_isinstance(self) -> None:
        assert has_capability(FakeReadTool(), ReadOnlyCapability) is True
        assert has_capability(FakeReadTool(), DestructiveCapability) is False

    def test_accepts_multiple_capabilities(self) -> None:
        tool = FakeRollbackableTool()
        assert has_capability(tool, DestructiveCapability) is True
        assert has_capability(tool, RollbackableCapability) is True

    def test_bare_tool_has_no_capabilities(self) -> None:
        tool = BareTool()
        for cap in (
            ReadOnlyCapability, DestructiveCapability,
            RollbackableCapability, NetworkCapability,
        ):
            assert has_capability(tool, cap) is False


# ---------- Live tools satisfy the Protocols ----------


class TestExistingToolsCompatible:
    """The built-in Tool base class exposes is_read_only / is_destructive
    as methods even when subclasses don't override them. Confirm this
    hasn't drifted so ReadOnlyCapability / DestructiveCapability still
    recognise stock tools without further plumbing."""

    def test_bash_tool_exposes_is_read_only(self) -> None:
        from llm_code.tools.bash import BashTool

        tool = BashTool()
        assert isinstance(tool, ReadOnlyCapability) or hasattr(
            tool, "is_read_only"
        )

    def test_read_file_tool_is_read_only_capable(self) -> None:
        """Spot-check: read_file really satisfies the Protocol."""
        try:
            from llm_code.tools.read_file import ReadFileTool
        except ImportError:
            pytest.skip("ReadFileTool not available in this build")
            return
        assert isinstance(ReadFileTool(), ReadOnlyCapability)
