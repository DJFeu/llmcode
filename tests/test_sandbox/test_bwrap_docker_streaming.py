"""Streaming support for Bwrap + Docker adapters (E4).

Bwrap runs under Popen we control, so it gets real line-by-line
streaming. Docker's current DockerSandbox.run() is blocking and
returns a single-shot result; rather than rewriting that runtime for
streaming we provide a *degraded* execute_streaming that emits the
entire output as one chunk once run() returns. Future work can
deepen Docker streaming via ``docker exec -i`` Popen.
"""
from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from llm_code.sandbox.policy_manager import (
    SandboxPolicy,
    has_streaming,
)
from llm_code.tools.sandbox import SandboxConfig


# ---------- Bwrap streaming (real per-line) ----------


@pytest.fixture
def bwrap_available(monkeypatch):
    monkeypatch.setattr(
        "llm_code.sandbox.bwrap.shutil.which",
        lambda name: "/usr/bin/bwrap" if name == "bwrap" else None,
    )


class TestBwrapStreaming:
    def test_exposes_streaming(self, bwrap_available) -> None:
        from llm_code.sandbox.bwrap import BwrapSandboxBackend
        assert has_streaming(BwrapSandboxBackend()) is True

    def test_emits_chunk_per_line(self, bwrap_available) -> None:
        from llm_code.sandbox.bwrap import BwrapSandboxBackend

        fake = MagicMock()
        fake.stdout = iter(["line1\n", "line2\n", "line3\n"])
        fake.wait.return_value = 0
        fake.returncode = 0

        chunks: list[str] = []
        with patch(
            "llm_code.sandbox.bwrap.subprocess.Popen",
            return_value=fake,
        ):
            result = BwrapSandboxBackend().execute_streaming(
                ["echo", "x"],
                SandboxPolicy(),
                on_chunk=chunks.append,
            )
        assert chunks == ["line1\n", "line2\n", "line3\n"]
        assert result.exit_code == 0
        assert result.stdout == "line1\nline2\nline3\n"

    def test_nonzero_exit_propagates(self, bwrap_available) -> None:
        from llm_code.sandbox.bwrap import BwrapSandboxBackend

        fake = MagicMock()
        fake.stdout = iter(["bwrap: cannot unshare\n"])
        fake.wait.return_value = 1
        fake.returncode = 1

        with patch(
            "llm_code.sandbox.bwrap.subprocess.Popen",
            return_value=fake,
        ):
            result = BwrapSandboxBackend().execute_streaming(
                ["rm", "/x"],
                SandboxPolicy(allow_write=False),
                on_chunk=lambda _s: None,
            )
        assert result.exit_code == 1
        assert "cannot unshare" in result.stdout

    def test_timeout_maps_to_124(self, bwrap_available) -> None:
        from llm_code.sandbox.bwrap import BwrapSandboxBackend

        fake = MagicMock()
        fake.stdout = iter(["long running...\n"])
        fake.wait.side_effect = subprocess.TimeoutExpired(cmd="bwrap", timeout=1)

        with patch(
            "llm_code.sandbox.bwrap.subprocess.Popen",
            return_value=fake,
        ):
            result = BwrapSandboxBackend(timeout_seconds=1).execute_streaming(
                ["sleep", "100"], SandboxPolicy(),
                on_chunk=lambda _s: None,
            )
        assert result.exit_code == 124
        assert "timed out" in result.stderr.lower()

    def test_callback_exception_swallowed(self, bwrap_available) -> None:
        from llm_code.sandbox.bwrap import BwrapSandboxBackend

        fake = MagicMock()
        fake.stdout = iter(["a\n", "b\n"])
        fake.wait.return_value = 0
        fake.returncode = 0

        def boom(_chunk):
            raise RuntimeError("ui crashed")

        with patch(
            "llm_code.sandbox.bwrap.subprocess.Popen",
            return_value=fake,
        ):
            result = BwrapSandboxBackend().execute_streaming(
                ["echo", "x"], SandboxPolicy(), on_chunk=boom,
            )
        assert result.exit_code == 0
        assert "a\nb\n" == result.stdout


# ---------- Docker streaming (degraded — single chunk) ----------


class TestDockerStreaming:
    def test_exposes_streaming(self) -> None:
        from llm_code.sandbox.adapters import DockerSandboxBackend

        cfg = SandboxConfig(enabled=True)
        with patch("llm_code.sandbox.adapters.DockerSandbox"):
            backend = DockerSandboxBackend(cfg)
        assert has_streaming(backend) is True

    # D1: ``test_streaming_degrades_to_single_chunk`` was removed. The
    # DockerSandboxBackend now streams real per-line output via
    # ``docker exec`` Popen. Happy-path behaviour is covered by
    # tests/test_sandbox/test_docker_real_streaming.py.

    def test_streaming_respects_policy_reject(self) -> None:
        """Policy enforcement gate from M2 still applies; when the gate
        rejects, no chunk is emitted and the caller sees the same
        exit_code=126 SandboxResult."""
        from llm_code.sandbox.adapters import DockerSandboxBackend

        cfg = SandboxConfig(enabled=True, network=True)
        mock_sb = MagicMock()

        chunks: list[str] = []
        with patch(
            "llm_code.sandbox.adapters.DockerSandbox",
            return_value=mock_sb,
        ):
            backend = DockerSandboxBackend(cfg)
            result = backend.execute_streaming(
                ["curl", "x"],
                SandboxPolicy(allow_network=False),
                on_chunk=chunks.append,
            )
        assert result.exit_code == 126
        assert chunks == []
        assert mock_sb.run.call_count == 0

    def test_empty_output_produces_no_chunk(self) -> None:
        from llm_code.sandbox.adapters import DockerSandboxBackend

        cfg = SandboxConfig(enabled=True, network=True)
        mock_sb = MagicMock()
        mock_sb._container_id = "abc"
        mock_sb._runtime_cmd = "docker"
        mock_sb.ensure_running.return_value = True

        fake_popen = MagicMock()
        fake_popen.stdout = iter(())
        fake_popen.wait.return_value = 0
        fake_popen.returncode = 0

        chunks: list[str] = []
        with patch(
            "llm_code.sandbox.adapters.DockerSandbox",
            return_value=mock_sb,
        ), patch(
            "llm_code.sandbox.adapters.subprocess.Popen",
            return_value=fake_popen,
        ):
            backend = DockerSandboxBackend(cfg)
            backend.execute_streaming(
                ["true"],
                SandboxPolicy(allow_network=True, allow_write=True),
                on_chunk=chunks.append,
            )
        # No output, no chunks (don't emit empty strings).
        assert chunks == []
