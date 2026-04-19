"""Tests for StreamingSandboxBackend Protocol + PTY streaming (E3)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from llm_code.sandbox.policy_manager import SandboxPolicy


# ---------- Protocol shape ----------


class TestStreamingProtocolShape:
    def test_protocol_exports_streaming(self) -> None:
        from llm_code.sandbox.policy_manager import StreamingSandboxBackend
        # ``issubclass`` against a Protocol with a data attribute raises
        # TypeError on Python 3.12+. The shape check we actually want
        # is "does the Protocol declare execute_streaming".
        assert "execute_streaming" in dir(StreamingSandboxBackend)

    def test_has_streaming_helper_detects(self) -> None:
        from llm_code.sandbox.policy_manager import has_streaming

        class _HasStreaming:
            name = "x"
            def execute(self, cmd, policy): ...
            def execute_streaming(self, cmd, policy, *, on_chunk): ...

        class _PlainOnly:
            name = "y"
            def execute(self, cmd, policy): ...

        assert has_streaming(_HasStreaming()) is True
        assert has_streaming(_PlainOnly()) is False

    def test_has_streaming_requires_callable(self) -> None:
        from llm_code.sandbox.policy_manager import has_streaming

        class _NotCallable:
            name = "z"
            def execute(self, cmd, policy): ...
            execute_streaming = "this is not a method"

        assert has_streaming(_NotCallable()) is False


# ---------- PtySandboxBackend.execute_streaming ----------


@pytest.fixture
def fake_pty_proc():
    """Emits three chunks then EOFs cleanly with exit status 0."""
    proc = MagicMock()
    reads = iter(["hello ", "streaming ", "world\n", EOFError()])
    alive_states = iter([True, True, True, True, False])

    def read_side_effect(n):
        item = next(reads)
        if isinstance(item, Exception):
            raise item
        return item

    proc.read.side_effect = read_side_effect
    proc.isalive.side_effect = lambda: next(alive_states)
    proc.exitstatus = 0
    return proc


class TestPtyStreaming:
    def test_execute_streaming_callback_invoked_per_chunk(
        self, fake_pty_proc,
    ) -> None:
        from llm_code.sandbox.adapters import PtySandboxBackend

        chunks: list[str] = []
        with patch(
            "llm_code.sandbox.adapters.PtyProcessUnicode",
            create=True,
        ) as mock_cls:
            mock_cls.spawn.return_value = fake_pty_proc
            result = PtySandboxBackend(timeout=5).execute_streaming(
                ["echo", "hi"],
                SandboxPolicy(),
                on_chunk=chunks.append,
            )
        assert chunks == ["hello ", "streaming ", "world\n"]
        assert result.exit_code == 0
        assert "".join(chunks) == result.stdout

    def test_execute_streaming_handles_ptyprocess_missing(self) -> None:
        from llm_code.sandbox.adapters import PtySandboxBackend

        def _no_pty_module():
            raise ImportError("ptyprocess not installed")

        with patch(
            "llm_code.sandbox.adapters._import_pty_process",
            side_effect=_no_pty_module,
        ):
            result = PtySandboxBackend().execute_streaming(
                ["echo"], SandboxPolicy(),
                on_chunk=lambda _s: None,
            )
        assert result.exit_code != 0
        assert "ptyprocess" in result.stderr.lower()

    def test_execute_streaming_failed_spawn_is_failure(self) -> None:
        from llm_code.sandbox.adapters import PtySandboxBackend

        with patch(
            "llm_code.sandbox.adapters.PtyProcessUnicode",
            create=True,
        ) as mock_cls:
            mock_cls.spawn.side_effect = OSError("no such file")
            result = PtySandboxBackend().execute_streaming(
                ["nonexistent"],
                SandboxPolicy(),
                on_chunk=lambda _s: None,
            )
        assert result.exit_code != 0
        assert "no such file" in result.stderr or "spawn" in result.stderr.lower()

    def test_callback_receives_str(self, fake_pty_proc) -> None:
        from llm_code.sandbox.adapters import PtySandboxBackend

        recorded: list[type] = []
        with patch(
            "llm_code.sandbox.adapters.PtyProcessUnicode",
            create=True,
        ) as mock_cls:
            mock_cls.spawn.return_value = fake_pty_proc
            PtySandboxBackend(timeout=5).execute_streaming(
                ["echo", "x"],
                SandboxPolicy(),
                on_chunk=lambda chunk: recorded.append(type(chunk)),
            )
        assert all(t is str for t in recorded)

    def test_callback_exceptions_do_not_abort_execution(self, fake_pty_proc) -> None:
        """A buggy callback mustn't break the executor — swallow and
        keep reading until the child dies."""
        from llm_code.sandbox.adapters import PtySandboxBackend

        def angry(chunk):  # noqa: ARG001
            raise RuntimeError("callback exploded")

        with patch(
            "llm_code.sandbox.adapters.PtyProcessUnicode",
            create=True,
        ) as mock_cls:
            mock_cls.spawn.return_value = fake_pty_proc
            result = PtySandboxBackend(timeout=5).execute_streaming(
                ["echo", "x"],
                SandboxPolicy(),
                on_chunk=angry,
            )
        # Execution itself succeeds; stdout still captured.
        assert result.exit_code == 0
        assert "world" in result.stdout or len(result.stdout) > 0


# ---------- has_streaming helper integration ----------


class TestHasStreamingOnRealBackends:
    def test_pty_backend_has_streaming(self) -> None:
        from llm_code.sandbox.adapters import PtySandboxBackend
        from llm_code.sandbox.policy_manager import has_streaming

        assert has_streaming(PtySandboxBackend()) is True
