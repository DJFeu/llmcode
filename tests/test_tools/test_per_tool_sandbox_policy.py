"""F4: Per-tool SandboxPolicy override."""
from __future__ import annotations

from unittest.mock import MagicMock

from llm_code.sandbox.policy_manager import SandboxPolicy, SandboxResult
from llm_code.tools.base import Tool
from llm_code.tools.bash import BashTool


class _NoopTool(Tool):
    """Minimal Tool subclass to exercise the base default."""
    @property
    def name(self): return "noop"
    @property
    def description(self): return "noop"
    @property
    def input_schema(self): return {"type": "object"}
    @property
    def required_permission(self):
        from llm_code.tools.base import PermissionLevel
        return PermissionLevel.READ_ONLY

    def execute(self, args, overlay=None):  # noqa: ARG002
        from llm_code.tools.base import ToolResult
        return ToolResult(output="noop")


class TestBaseDefault:
    def test_base_default_is_none(self) -> None:
        """Tools that don't opt in get None — caller picks."""
        assert _NoopTool().default_sandbox_policy() is None


class TestBashToolDefault:
    def test_bash_default_is_workspace_plus_network(self) -> None:
        policy = BashTool().default_sandbox_policy()
        assert policy is not None
        assert policy.allow_read is True
        assert policy.allow_write is True
        assert policy.allow_network is True


class TestPolicyPropagation:
    def test_bash_backend_execution_uses_tool_default(self) -> None:
        """When BashTool dispatches through a streaming backend, the
        policy passed to execute_streaming must match whatever
        ``default_sandbox_policy`` returned."""
        captured: dict = {}

        def capture_stream(cmd, policy, *, on_chunk):  # noqa: ARG001
            captured["policy"] = policy
            return SandboxResult(exit_code=0, stdout="", stderr="")

        backend = MagicMock(spec=["name", "execute", "execute_streaming"])
        backend.name = "pty"
        backend.execute_streaming.side_effect = capture_stream

        tool = BashTool(sandbox=backend)
        tool.execute_with_progress({"command": "echo x"}, on_progress=lambda _p: None)

        got = captured.get("policy")
        assert isinstance(got, SandboxPolicy)
        assert got == tool.default_sandbox_policy()


class TestSubclassOverride:
    def test_subclass_can_tighten_policy(self) -> None:
        class ReadOnlyBash(BashTool):
            def default_sandbox_policy(self):
                return SandboxPolicy(
                    allow_read=True, allow_write=False, allow_network=False,
                )

        captured: dict = {}

        def capture_stream(cmd, policy, *, on_chunk):  # noqa: ARG001
            captured["policy"] = policy
            return SandboxResult(exit_code=0, stdout="", stderr="")

        backend = MagicMock(spec=["name", "execute", "execute_streaming"])
        backend.name = "pty"
        backend.execute_streaming.side_effect = capture_stream

        ReadOnlyBash(sandbox=backend).execute_with_progress(
            {"command": "ls"}, on_progress=lambda _p: None,
        )

        got = captured["policy"]
        assert got.allow_write is False
        assert got.allow_network is False
