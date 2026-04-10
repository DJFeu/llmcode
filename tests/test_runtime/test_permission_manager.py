"""Tests for the extracted PermissionManager."""
from __future__ import annotations

from unittest.mock import MagicMock



def test_permission_manager_exists():
    from llm_code.runtime.permission_manager import PermissionManager
    policy = MagicMock()
    session = MagicMock()
    mgr = PermissionManager(policy, session)
    assert mgr._policy is policy


def test_is_session_allowed_tool_in_allowlist():
    from llm_code.runtime.permission_manager import PermissionManager
    policy = MagicMock()
    session = MagicMock()
    mgr = PermissionManager(policy, session)
    mgr._session_allowed_tools.add("read_file")
    assert mgr.is_session_allowed("read_file", "{}") is True
    assert mgr.is_session_allowed("write_file", "{}") is False


def test_is_session_allowed_exact():
    from llm_code.runtime.permission_manager import PermissionManager
    policy = MagicMock()
    session = MagicMock()
    mgr = PermissionManager(policy, session)
    mgr._session_allowed_exact.add(("bash", '{"command": "ls"}'))
    assert mgr.is_session_allowed("bash", '{"command": "ls"}') is True
    assert mgr.is_session_allowed("bash", '{"command": "rm"}') is False


def test_is_session_allowed_bash_prefix():
    from llm_code.runtime.permission_manager import PermissionManager
    policy = MagicMock()
    session = MagicMock()
    mgr = PermissionManager(policy, session)
    mgr._session_allowed_prefixes.add("git ")
    assert mgr.is_session_allowed("bash", "{}", {"command": "git status"}) is True
    assert mgr.is_session_allowed("bash", "{}", {"command": "rm -rf /"}) is False


def test_record_choice_always_kind():
    from llm_code.runtime.permission_manager import PermissionManager
    policy = MagicMock()
    session = MagicMock()
    mgr = PermissionManager(policy, session)
    mgr.record_permission_choice("always_kind", "bash", '{"command": "git status"}', {"command": "git status"})
    assert "bash" in mgr._session_allowed_tools
    assert "git " in mgr._session_allowed_prefixes


def test_record_choice_always_exact():
    from llm_code.runtime.permission_manager import PermissionManager
    policy = MagicMock()
    session = MagicMock()
    mgr = PermissionManager(policy, session)
    mgr.record_permission_choice("always_exact", "bash", '{"command": "ls"}')
    assert ("bash", '{"command": "ls"}') in mgr._session_allowed_exact


def test_send_permission_response_edit():
    """send_permission_response with 'edit' encodes args into the future."""
    import asyncio
    from llm_code.runtime.permission_manager import PermissionManager

    policy = MagicMock()
    session = MagicMock()
    mgr = PermissionManager(policy, session)

    loop = asyncio.new_event_loop()
    mgr._permission_future = loop.create_future()
    mgr.send_permission_response("edit", edited_args={"command": "echo hi"})
    result = loop.run_until_complete(mgr._permission_future)
    assert result.startswith("edit:")
    loop.close()


def test_set_mcp_callbacks():
    from llm_code.runtime.permission_manager import PermissionManager
    policy = MagicMock()
    session = MagicMock()
    mgr = PermissionManager(policy, session)
    cb = MagicMock()
    sink = MagicMock()
    mgr.set_mcp_approval_callback(cb)
    mgr.set_mcp_event_sink(sink)
    assert mgr._mcp_approval_callback is cb
    assert mgr._mcp_event_sink is sink
