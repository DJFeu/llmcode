"""Tests for BashTool opt-in routing to SandboxBackend Protocol (M3)."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from llm_code.sandbox.policy_manager import SandboxPolicy, SandboxResult
from llm_code.tools.bash import BashTool


def _make_backend(
    *, name: str = "pty",
    exit_code: int = 0, stdout: str = "", stderr: str = "",
) -> MagicMock:
    """Mock that matches SandboxBackend Protocol *exactly*.

    ``spec`` restricts attributes to ``name`` + ``execute`` so the
    runtime-checkable Protocol dispatches to the new path and no
    legacy DockerSandbox methods (``ensure_running`` etc.) leak through
    auto-attribute generation.
    """
    backend = MagicMock(spec=["name", "execute"])
    backend.name = name
    backend.execute.return_value = SandboxResult(
        exit_code=exit_code, stdout=stdout, stderr=stderr,
    )
    return backend


@pytest.fixture
def backend_returning_ok() -> MagicMock:
    return _make_backend(stdout="hello")


class TestBackendDetection:
    def test_legacy_docker_sandbox_still_uses_is_available_path(self) -> None:
        legacy = MagicMock(spec=["is_available", "ensure_running", "run"])
        legacy.is_available.return_value = True
        legacy.ensure_running.return_value = True
        legacy.run.return_value = MagicMock(
            stdout="legacy-out\n", stderr="", returncode=0, timed_out=False,
        )

        tool = BashTool(sandbox=legacy)
        result = tool.execute({"command": "echo hi"})
        assert result.is_error is False
        assert "legacy-out" in result.output
        legacy.is_available.assert_called_once()
        legacy.ensure_running.assert_called_once()
        legacy.run.assert_called_once()

    def test_sandbox_backend_uses_execute_path(self, backend_returning_ok) -> None:
        tool = BashTool(sandbox=backend_returning_ok)
        result = tool.execute({"command": "echo hello"})
        assert result.is_error is False
        assert "hello" in result.output
        assert backend_returning_ok.execute.call_count == 1


class TestBackendExecution:
    def test_execute_receives_policy(self) -> None:
        backend = _make_backend(name="docker")
        tool = BashTool(sandbox=backend)
        tool.execute({"command": "ls"})

        call_args = backend.execute.call_args
        assert call_args is not None
        args = call_args.args
        kwargs = call_args.kwargs
        # Backend must be called with (command_list, policy)
        cmd_arg = args[0] if len(args) >= 1 else None
        policy_arg = (
            args[1] if len(args) >= 2 else kwargs.get("policy")
        )
        assert isinstance(cmd_arg, list)
        assert cmd_arg[0] in ("sh", "bash")
        assert isinstance(policy_arg, SandboxPolicy)

    def test_nonzero_exit_becomes_error_tool_result(self) -> None:
        backend = _make_backend(exit_code=2, stderr="oops")
        tool = BashTool(sandbox=backend)
        result = tool.execute({"command": "false"})
        assert result.is_error is True
        assert "oops" in result.output

    def test_sandbox_metadata_marks_backend_name(self) -> None:
        backend = _make_backend(stdout="x")
        tool = BashTool(sandbox=backend)
        result = tool.execute({"command": "echo x"})
        md = result.metadata or {}
        assert md.get("sandbox") == "pty"


class TestDefaultPathUnchanged:
    def test_no_sandbox_falls_through_to_host(self, monkeypatch) -> None:
        import subprocess

        captured = {}
        real_run = subprocess.run

        def spy_run(*args, **kwargs):
            captured["called"] = True
            return real_run(*args, **kwargs)

        monkeypatch.setattr(subprocess, "run", spy_run)

        tool = BashTool()
        result = tool.execute({"command": "echo default-path-ok"})
        assert captured.get("called") is True
        assert "default-path-ok" in result.output
        assert result.is_error is False
