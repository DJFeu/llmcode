"""F2: BashTool.execute_with_progress routes to streaming backend."""
from __future__ import annotations

from unittest.mock import MagicMock


from llm_code.sandbox.policy_manager import SandboxResult
from llm_code.tools.base import ToolProgress
from llm_code.tools.bash import BashTool


def _streaming_backend(chunks: list[str], *, exit_code: int = 0):
    b = MagicMock(spec=["name", "execute", "execute_streaming"])
    b.name = "pty"

    def fake_stream(cmd, policy, *, on_chunk):  # noqa: ARG001
        for c in chunks:
            on_chunk(c)
        return SandboxResult(
            exit_code=exit_code, stdout="".join(chunks), stderr="",
        )
    b.execute_streaming.side_effect = fake_stream
    return b


class TestStreamingProgressRoute:
    def test_backend_streaming_emits_tool_progress_per_chunk(self) -> None:
        backend = _streaming_backend(["line1\n", "line2\n", "line3\n"])
        tool = BashTool(sandbox=backend)

        progress: list[ToolProgress] = []
        result = tool.execute_with_progress(
            {"command": "echo x", "timeout": 5},
            on_progress=progress.append,
        )
        # One ToolProgress per chunk
        assert len(progress) == 3
        assert all(p.tool_name == "bash" for p in progress)
        assert result.is_error is False
        md = result.metadata or {}
        assert md.get("sandbox") == "pty"

    def test_backend_nonzero_exit_surfaces_error(self) -> None:
        backend = _streaming_backend(["oops\n"], exit_code=2)
        tool = BashTool(sandbox=backend)

        progress: list[ToolProgress] = []
        result = tool.execute_with_progress(
            {"command": "false", "timeout": 5},
            on_progress=progress.append,
        )
        assert result.is_error is True
        assert "oops" in result.output

    def test_legacy_docker_still_uses_legacy_path(self) -> None:
        """A backend with ``ensure_running`` is the legacy DockerSandbox
        shape — must NOT be routed to execute_streaming even if a run()
        method is present."""
        legacy = MagicMock(spec=["is_available", "ensure_running", "run"])
        legacy.is_available.return_value = True
        legacy.ensure_running.return_value = True
        legacy.run.return_value = MagicMock(
            stdout="legacy\n", stderr="", returncode=0, timed_out=False,
        )

        tool = BashTool(sandbox=legacy)
        progress: list[ToolProgress] = []
        result = tool.execute_with_progress(
            {"command": "echo legacy-shape-ok"},
            on_progress=progress.append,
        )
        # Command executed on host path; the legacy backend never got
        # an execute_streaming call because it doesn't expose one.
        assert "legacy-shape-ok" in result.output
        assert result.is_error is False


class TestDefaultPathPreserved:
    def test_no_sandbox_falls_through_to_host(self, monkeypatch) -> None:
        import subprocess

        real_popen = subprocess.Popen
        captured = {"count": 0}

        def spy_popen(*args, **kwargs):
            captured["count"] += 1
            return real_popen(*args, **kwargs)

        monkeypatch.setattr(subprocess, "Popen", spy_popen)

        tool = BashTool()
        progress: list[ToolProgress] = []
        result = tool.execute_with_progress(
            {"command": "echo default-path"},
            on_progress=progress.append,
        )
        assert captured["count"] >= 1
        assert "default-path" in result.output
        assert result.is_error is False
