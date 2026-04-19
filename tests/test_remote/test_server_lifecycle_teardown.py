"""G2: RemoteServer.start() closes runtime on exit."""
from __future__ import annotations

import inspect
from unittest.mock import MagicMock

import pytest


class TestStartTearsDownRuntime:
    def test_source_has_finally_runtime_shutdown(self) -> None:
        """Source check: start()'s finally block calls runtime.shutdown().
        End-to-end drive would require mocking websockets.serve + an
        asyncio loop teardown cycle — disproportionate for the wire."""
        from llm_code.remote.server import RemoteServer

        src = inspect.getsource(RemoteServer.start)
        assert "finally" in src, (
            "G2: RemoteServer.start should use try/finally so a "
            "SIGTERM-style exit still triggers runtime cleanup."
        )
        assert "_runtime" in src and "shutdown" in src, (
            "G2: the finally block must call self._runtime.shutdown()."
        )

    def test_shutdown_guarded(self) -> None:
        from llm_code.remote.server import RemoteServer

        src = inspect.getsource(RemoteServer.start)
        # Find the actual call — the first mention may be in the
        # docstring where we merely describe the behaviour.
        shutdown_idx = src.rfind("self._runtime.shutdown()")
        assert shutdown_idx > 0
        snippet = src[max(0, shutdown_idx - 200): shutdown_idx + 200]
        # Same safety pattern as the REPL — swallow teardown errors.
        assert "try:" in snippet and "except" in snippet

    @pytest.mark.asyncio
    async def test_runtime_shutdown_invoked_when_serve_exits(
        self, monkeypatch,
    ) -> None:
        """Integration-ish: patch websockets.serve to exit quickly and
        asyncio.Future to resolve, confirm runtime.shutdown fires."""
        from llm_code.remote.server import RemoteServer

        server = RemoteServer(host="x", port=0, config=None)
        # Attach a fake runtime after manual construction; normally
        # _init_session would set it, but we short-circuit the serve
        # loop so that code never runs.
        server._runtime = MagicMock()

        class _FakeServeCtx:
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                return False

        def fake_serve(*args, **kwargs):  # noqa: ARG001
            return _FakeServeCtx()

        async def short_future():
            return None  # immediately resolves so start() exits

        monkeypatch.setattr(
            "llm_code.remote.server.websockets.serve", fake_serve,
        )
        monkeypatch.setattr(
            "llm_code.remote.server.asyncio.Future", short_future,
        )

        await server.start()
        server._runtime.shutdown.assert_called_once()
