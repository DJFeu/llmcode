"""Real per-line Docker streaming via ``docker exec`` Popen (D1)."""
from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from llm_code.sandbox.policy_manager import SandboxPolicy
from llm_code.tools.sandbox import SandboxConfig


def _mock_docker_sandbox(
    *,
    container_id: str = "abc123",
    runtime_cmd: str = "docker",
    ensure_running: bool = True,
):
    sb = MagicMock()
    sb._container_id = container_id
    sb._runtime_cmd = runtime_cmd
    sb.ensure_running.return_value = ensure_running
    return sb


def _popen_iter(lines: list[str], *, returncode: int = 0):
    """Build a MagicMock Popen whose stdout iterates the given lines."""
    proc = MagicMock()
    proc.stdout = iter(lines)
    proc.wait.return_value = returncode
    proc.returncode = returncode
    return proc


@pytest.fixture
def matching_policy() -> SandboxPolicy:
    return SandboxPolicy(allow_network=True, allow_write=True)


class TestRealDockerStreaming:
    def test_emits_chunk_per_line(self, matching_policy) -> None:
        from llm_code.sandbox.adapters import DockerSandboxBackend

        cfg = SandboxConfig(enabled=True, network=True, mount_readonly=False)
        sb = _mock_docker_sandbox()

        chunks: list[str] = []
        with patch(
            "llm_code.sandbox.adapters.DockerSandbox",
            return_value=sb,
        ), patch(
            "llm_code.sandbox.adapters.subprocess.Popen",
            return_value=_popen_iter(["a\n", "b\n", "c\n"]),
        ):
            backend = DockerSandboxBackend(cfg)
            result = backend.execute_streaming(
                ["echo", "abc"], matching_policy,
                on_chunk=chunks.append,
            )
        assert chunks == ["a\n", "b\n", "c\n"]
        assert result.exit_code == 0
        assert result.stdout == "a\nb\nc\n"

    def test_uses_runtime_cmd_exec(self, matching_policy) -> None:
        from llm_code.sandbox.adapters import DockerSandboxBackend

        cfg = SandboxConfig(enabled=True, network=True, mount_readonly=False)
        sb = _mock_docker_sandbox(runtime_cmd="podman", container_id="xyz")

        with patch(
            "llm_code.sandbox.adapters.DockerSandbox",
            return_value=sb,
        ), patch(
            "llm_code.sandbox.adapters.subprocess.Popen",
            return_value=_popen_iter(["ok\n"]),
        ) as mock_popen:
            DockerSandboxBackend(cfg).execute_streaming(
                ["echo", "ok"], matching_policy, on_chunk=lambda _s: None,
            )
        args = mock_popen.call_args.args[0]
        # First argv is the runtime binary
        assert args[0] == "podman"
        assert args[1] == "exec"
        assert "xyz" in args

    def test_timeout_kills_process(self, matching_policy) -> None:
        from llm_code.sandbox.adapters import DockerSandboxBackend

        cfg = SandboxConfig(enabled=True, network=True, mount_readonly=False)
        sb = _mock_docker_sandbox()
        proc = MagicMock()
        proc.stdout = iter(["slow...\n"])
        proc.wait.side_effect = subprocess.TimeoutExpired(cmd="docker", timeout=1)

        with patch(
            "llm_code.sandbox.adapters.DockerSandbox",
            return_value=sb,
        ), patch(
            "llm_code.sandbox.adapters.subprocess.Popen",
            return_value=proc,
        ):
            backend = DockerSandboxBackend(cfg, timeout_seconds=1)
            result = backend.execute_streaming(
                ["sleep", "100"], matching_policy,
                on_chunk=lambda _s: None,
            )
        assert result.exit_code == 124
        assert "timed out" in result.stderr.lower()
        proc.kill.assert_called_once()

    def test_policy_reject_short_circuits_before_popen(
        self, matching_policy,
    ) -> None:
        """M2 gate still fires — when policy is stricter than container
        launch config, Popen is never invoked and exit_code=126."""
        from llm_code.sandbox.adapters import DockerSandboxBackend

        cfg = SandboxConfig(enabled=True, network=True, mount_readonly=False)

        with patch(
            "llm_code.sandbox.adapters.DockerSandbox",
            return_value=_mock_docker_sandbox(),
        ), patch(
            "llm_code.sandbox.adapters.subprocess.Popen",
        ) as mock_popen:
            backend = DockerSandboxBackend(cfg)
            result = backend.execute_streaming(
                ["curl", "x"],
                SandboxPolicy(allow_network=False, allow_write=True),
                on_chunk=lambda _s: None,
            )
        assert result.exit_code == 126
        assert mock_popen.call_count == 0

    def test_container_start_failure_surfaces(self, matching_policy) -> None:
        from llm_code.sandbox.adapters import DockerSandboxBackend

        cfg = SandboxConfig(enabled=True, network=True, mount_readonly=False)
        sb = _mock_docker_sandbox(ensure_running=False)

        with patch(
            "llm_code.sandbox.adapters.DockerSandbox",
            return_value=sb,
        ), patch(
            "llm_code.sandbox.adapters.subprocess.Popen",
        ) as mock_popen:
            backend = DockerSandboxBackend(cfg)
            result = backend.execute_streaming(
                ["ls"], matching_policy, on_chunk=lambda _s: None,
            )
        assert result.exit_code != 0
        assert "container" in result.stderr.lower()
        assert mock_popen.call_count == 0

    def test_callback_exception_swallowed(self, matching_policy) -> None:
        from llm_code.sandbox.adapters import DockerSandboxBackend

        cfg = SandboxConfig(enabled=True, network=True, mount_readonly=False)
        sb = _mock_docker_sandbox()

        def boom(_chunk):
            raise RuntimeError("ui died")

        with patch(
            "llm_code.sandbox.adapters.DockerSandbox",
            return_value=sb,
        ), patch(
            "llm_code.sandbox.adapters.subprocess.Popen",
            return_value=_popen_iter(["x\n", "y\n"]),
        ):
            backend = DockerSandboxBackend(cfg)
            result = backend.execute_streaming(
                ["echo", "x"], matching_policy, on_chunk=boom,
            )
        # Despite a broken callback, execution still completes cleanly.
        assert result.exit_code == 0
        assert result.stdout == "x\ny\n"

    def test_popen_spawn_failure_degrades_to_error(
        self, matching_policy,
    ) -> None:
        from llm_code.sandbox.adapters import DockerSandboxBackend

        cfg = SandboxConfig(enabled=True, network=True, mount_readonly=False)
        sb = _mock_docker_sandbox()

        with patch(
            "llm_code.sandbox.adapters.DockerSandbox",
            return_value=sb,
        ), patch(
            "llm_code.sandbox.adapters.subprocess.Popen",
            side_effect=OSError("no runtime on PATH"),
        ):
            backend = DockerSandboxBackend(cfg)
            result = backend.execute_streaming(
                ["ls"], matching_policy, on_chunk=lambda _s: None,
            )
        assert result.exit_code != 0
        assert "PATH" in result.stderr or "spawn" in result.stderr.lower()
