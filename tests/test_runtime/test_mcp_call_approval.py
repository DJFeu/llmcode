"""Per-call MCP approval (v16 M10).

Covers stable args hashing, scope semantics (one vs session), tool-
level grants, revocation, and the integration point between
:class:`MCPCallApproval` and the existing ``PermissionPolicy``.
"""
from __future__ import annotations

from llm_code.runtime.permissions import (
    MCPCallApproval,
    MCPCallApprovalGrant,
    args_hash,
)


def test_args_hash_stable_for_dict_order() -> None:
    a = {"a": 1, "b": 2}
    b = {"b": 2, "a": 1}
    assert args_hash(a) == args_hash(b)


def test_args_hash_differs_for_different_args() -> None:
    assert args_hash({"a": 1}) != args_hash({"a": 2})


def test_args_hash_handles_non_serialisable() -> None:
    class X:
        def __repr__(self) -> str:
            return "X"

    h1 = args_hash({"k": X()})
    h2 = args_hash({"k": X()})
    assert h1 == h2  # same str(args) yields same hash


def test_approve_call_once_consumed_on_match() -> None:
    approval = MCPCallApproval()
    approval.approve_call("tool_a", {"path": "/x"})
    assert approval.check("tool_a", {"path": "/x"}) is True
    # Second call with same args is denied (one-shot consumed)
    assert approval.check("tool_a", {"path": "/x"}) is False


def test_approve_call_session_persists() -> None:
    approval = MCPCallApproval()
    approval.approve_call("tool_a", {"path": "/x"}, scope="session")
    for _ in range(5):
        assert approval.check("tool_a", {"path": "/x"}) is True


def test_approve_call_does_not_unlock_other_args() -> None:
    approval = MCPCallApproval()
    approval.approve_call("tool_a", {"path": "/x"})
    assert approval.check("tool_a", {"path": "/y"}) is False


def test_approve_tool_short_circuits_args_check() -> None:
    approval = MCPCallApproval()
    approval.approve_tool("tool_a")
    assert approval.check("tool_a", {"path": "/x"}) is True
    assert approval.check("tool_a", {"different": "args"}) is True
    # Other tools still gated
    assert approval.check("tool_b", {}) is False


def test_revoke_clears_grants() -> None:
    approval = MCPCallApproval()
    approval.approve_tool("tool_a")
    approval.approve_call("tool_a", {"x": 1}, scope="session")
    approval.revoke_tool("tool_a")
    assert approval.check("tool_a", {"x": 1}) is False
    assert "tool_a" not in approval.list_tool_grants()


def test_list_grants_returns_records() -> None:
    approval = MCPCallApproval()
    approval.approve_call("tool_a", {"x": 1})
    grants = approval.list_grants()
    assert len(grants) == 1
    assert isinstance(grants[0], MCPCallApprovalGrant)
    assert grants[0].tool_name == "tool_a"


def test_reset_clears_state() -> None:
    approval = MCPCallApproval()
    approval.approve_tool("a")
    approval.approve_call("b", {"x": 1})
    approval.reset()
    assert approval.list_grants() == []
    assert approval.list_tool_grants() == []


def test_is_tool_approved_reflects_session_grant() -> None:
    approval = MCPCallApproval()
    assert approval.is_tool_approved("tool_a") is False
    approval.approve_tool("tool_a")
    assert approval.is_tool_approved("tool_a") is True
