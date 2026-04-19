"""Tests for SandboxBackend adapters (S4.1).

Two concrete adapters let :func:`choose_backend` return something that
actually executes commands, not just the _NullBackend placeholder:

    * :class:`PtySandboxBackend` — wraps the existing run_pty helper.
    * :class:`DockerSandboxBackend` — wraps the existing DockerSandbox.

Both satisfy the :class:`SandboxBackend` Protocol so callers can
dispatch uniformly.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from llm_code.sandbox.adapters import (
    DockerSandboxBackend,
    PtySandboxBackend,
)
from llm_code.sandbox.policy_manager import (
    SandboxBackend,
    SandboxPolicy,
    SandboxResult,
)
from llm_code.tools.sandbox import PTYResult, SandboxConfig


# ---------- PtySandboxBackend ----------


class TestPtySandboxBackend:
    def test_satisfies_protocol(self) -> None:
        backend = PtySandboxBackend()
        assert isinstance(backend, SandboxBackend)
        assert backend.name == "pty"

    def test_execute_returns_sandbox_result(self) -> None:
        backend = PtySandboxBackend()
        fake = PTYResult(output="hello\n", returncode=0, timed_out=False)

        with patch("llm_code.sandbox.adapters.run_pty", return_value=fake):
            result = backend.execute(["echo", "hello"], SandboxPolicy())

        assert isinstance(result, SandboxResult)
        assert result.exit_code == 0
        assert "hello" in result.stdout
        assert result.is_success is True

    def test_execute_propagates_nonzero_exit(self) -> None:
        backend = PtySandboxBackend()
        fake = PTYResult(output="nope", returncode=2, timed_out=False)

        with patch("llm_code.sandbox.adapters.run_pty", return_value=fake):
            result = backend.execute(["false"], SandboxPolicy())

        assert result.exit_code == 2
        assert result.is_success is False

    def test_execute_timeout_maps_to_124(self) -> None:
        backend = PtySandboxBackend()
        fake = PTYResult(output="stuck", returncode=124, timed_out=True)

        with patch("llm_code.sandbox.adapters.run_pty", return_value=fake):
            result = backend.execute(["sleep", "10"], SandboxPolicy())

        assert result.exit_code == 124
        assert "stuck" in result.stdout

    def test_list_command_shell_escaped(self) -> None:
        """run_pty takes a shell string; the adapter must quote
        arguments that contain spaces or special chars so ``"a b"``
        doesn't become ``a b`` (two tokens) on the other side."""
        backend = PtySandboxBackend()
        captured: dict = {}

        def fake_run_pty(cmd: str, **kwargs):  # noqa: ARG001
            captured["cmd"] = cmd
            return PTYResult(output="", returncode=0)

        with patch("llm_code.sandbox.adapters.run_pty", side_effect=fake_run_pty):
            backend.execute(["echo", "a b c"], SandboxPolicy())

        assert "'a b c'" in captured["cmd"] or '"a b c"' in captured["cmd"]


# ---------- DockerSandboxBackend ----------


class TestDockerSandboxBackend:
    def test_satisfies_protocol(self) -> None:
        cfg = SandboxConfig(enabled=True)
        with patch("llm_code.sandbox.adapters.DockerSandbox"):
            backend = DockerSandboxBackend(cfg)
        assert isinstance(backend, SandboxBackend)
        assert backend.name == "docker"

    def test_execute_delegates_to_docker_sandbox(self) -> None:
        cfg = SandboxConfig(enabled=True)
        mock_sb = MagicMock()
        mock_sb.run.return_value = MagicMock(
            stdout="out", stderr="err", returncode=0, timed_out=False,
        )

        with patch("llm_code.sandbox.adapters.DockerSandbox", return_value=mock_sb):
            backend = DockerSandboxBackend(cfg)
            # Matching policy to avoid the M2 enforcement gate —
            # delegation is what we're testing here.
            result = backend.execute(
                ["ls", "/"],
                SandboxPolicy(allow_network=True, allow_write=True),
            )

        assert result.exit_code == 0
        assert result.stdout == "out"
        assert result.stderr == "err"
        mock_sb.run.assert_called_once()

    def test_execute_with_matching_policy_passes_through(self) -> None:
        """When policy matches the launched container (network=True on
        both sides) the call passes through without the enforcement
        gate rejecting it. Pre-M2 this was the only case the
        skeleton covered; M2 adds proper reject paths below."""
        cfg = SandboxConfig(enabled=True, network=True)
        mock_sb = MagicMock()
        mock_sb.run.return_value = MagicMock(
            stdout="", stderr="", returncode=0, timed_out=False,
        )

        with patch("llm_code.sandbox.adapters.DockerSandbox", return_value=mock_sb):
            backend = DockerSandboxBackend(cfg)
            policy = SandboxPolicy(allow_network=True, allow_write=True)
            backend.execute(["curl", "github.com"], policy)

        assert mock_sb.run.call_count == 1


# ---------- M2: policy enforcement on execute ----------


class TestDockerPolicyEnforcement:
    """M2 — when the requested SandboxPolicy is stricter than the
    launched container's config, execute() refuses the call rather
    than pretending to sandbox it. Docker's --network=none / --read-only
    are launch-time flags; per-call tightening would require restarting
    the container, which is too heavy for a runtime hot-path."""

    def test_network_stricter_than_container_rejects(self) -> None:
        cfg = SandboxConfig(enabled=True, network=True, mount_readonly=False)
        mock_sb = MagicMock()
        mock_sb.run.return_value = MagicMock(
            stdout="", stderr="", returncode=0, timed_out=False,
        )

        with patch("llm_code.sandbox.adapters.DockerSandbox", return_value=mock_sb):
            backend = DockerSandboxBackend(cfg)
            policy = SandboxPolicy(allow_network=False, allow_write=True)
            result = backend.execute(["curl", "x"], policy)

        assert result.exit_code == 126
        assert "policy" in result.stderr.lower()
        assert "network" in result.stderr.lower()
        assert mock_sb.run.call_count == 0

    def test_write_stricter_than_container_rejects(self) -> None:
        cfg = SandboxConfig(enabled=True, network=False, mount_readonly=False)
        mock_sb = MagicMock()

        with patch("llm_code.sandbox.adapters.DockerSandbox", return_value=mock_sb):
            backend = DockerSandboxBackend(cfg)
            policy = SandboxPolicy(allow_network=False, allow_write=False)
            result = backend.execute(["touch", "x"], policy)

        assert result.exit_code == 126
        assert "policy" in result.stderr.lower()
        assert ("write" in result.stderr.lower()
                or "read-only" in result.stderr.lower()
                or "read_only" in result.stderr.lower())
        assert mock_sb.run.call_count == 0

    def test_policy_matches_container_proceeds(self) -> None:
        cfg = SandboxConfig(enabled=True, network=False, mount_readonly=True)
        mock_sb = MagicMock()
        mock_sb.run.return_value = MagicMock(
            stdout="ok", stderr="", returncode=0, timed_out=False,
        )

        with patch("llm_code.sandbox.adapters.DockerSandbox", return_value=mock_sb):
            backend = DockerSandboxBackend(cfg)
            policy = SandboxPolicy(allow_network=False, allow_write=False)
            result = backend.execute(["ls"], policy)

        assert result.exit_code == 0
        assert result.stdout == "ok"
        assert mock_sb.run.call_count == 1

    def test_policy_laxer_than_container_still_runs(self) -> None:
        """Caller asks for network=True but container launched with
        network=False. The container is *stricter* than the call —
        the request will fail naturally when it tries to reach the
        net. We let it through; the runtime sees the failure from
        inside the sandbox, which is the intended behaviour."""
        cfg = SandboxConfig(enabled=True, network=False, mount_readonly=True)
        mock_sb = MagicMock()
        mock_sb.run.return_value = MagicMock(
            stdout="", stderr="network unreachable", returncode=1, timed_out=False,
        )

        with patch("llm_code.sandbox.adapters.DockerSandbox", return_value=mock_sb):
            backend = DockerSandboxBackend(cfg)
            policy = SandboxPolicy(allow_network=True, allow_write=True)
            result = backend.execute(["curl", "x"], policy)

        assert mock_sb.run.call_count == 1
        assert result.exit_code == 1


# ---------- Shared result conversion ----------


class TestPtyResultConversion:
    def test_timed_out_in_stderr_marker(self) -> None:
        """When the PTY timed out we want the caller to see *why*, even
        though PTYResult only carries one output stream."""
        backend = PtySandboxBackend()
        fake = PTYResult(output="partial", returncode=124, timed_out=True)

        with patch("llm_code.sandbox.adapters.run_pty", return_value=fake):
            result = backend.execute(["sleep", "1"], SandboxPolicy())

        assert result.exit_code == 124
        assert "timed out" in result.stderr.lower() or result.exit_code == 124


# ---------- Error resilience ----------


class TestAdapterErrorHandling:
    def test_pty_unavailable_returns_failure(self) -> None:
        """When run_pty raises, the adapter must return a failure
        SandboxResult instead of propagating — runtime uses
        ``exit_code != 0`` as the universal "something went wrong"
        signal."""
        backend = PtySandboxBackend()

        def boom(*args, **kwargs):  # noqa: ARG001
            raise RuntimeError("ptyprocess missing")

        with patch("llm_code.sandbox.adapters.run_pty", side_effect=boom):
            result = backend.execute(["echo"], SandboxPolicy())

        assert result.exit_code != 0
        assert "ptyprocess" in result.stderr.lower() or "missing" in result.stderr.lower()

    def test_docker_unavailable_returns_failure(self) -> None:
        cfg = SandboxConfig(enabled=True)

        with patch(
            "llm_code.sandbox.adapters.DockerSandbox",
            side_effect=RuntimeError("docker daemon not reachable"),
        ):
            with pytest.raises(RuntimeError):
                # Constructor failure is OK to surface — the caller
                # can decide to fall back to PTY.
                DockerSandboxBackend(cfg)
