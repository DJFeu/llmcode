"""Tests for the ported debug REPL server (M4.11 parity)."""
from __future__ import annotations

import inspect
import sys
from unittest.mock import MagicMock

import pytest


class TestDebugReplServerLifecycle:
    def test_source_has_finally_runtime_shutdown(self) -> None:
        """Mirrors the test previously under tests/test_remote/.

        The legacy contract: ``start()`` must call ``runtime.shutdown()``
        from a finally block so SIGTERM / KeyboardInterrupt exits still
        tear the runtime down cleanly.
        """
        from llm_code.hayhooks.debug_repl import DebugReplServer

        src = inspect.getsource(DebugReplServer.start)
        assert "finally" in src, (
            "DebugReplServer.start must wrap the run loop in try/finally"
        )
        assert "_runtime" in src and "shutdown" in src, (
            "finally block must invoke self._runtime.shutdown()"
        )

    def test_shutdown_guarded(self) -> None:
        from llm_code.hayhooks.debug_repl import DebugReplServer

        src = inspect.getsource(DebugReplServer.start)
        idx = src.rfind("self._runtime.shutdown()")
        assert idx > 0
        snippet = src[max(0, idx - 200): idx + 200]
        assert "try:" in snippet and "except" in snippet

    @pytest.mark.asyncio
    async def test_runtime_shutdown_invoked_when_serve_exits(
        self, monkeypatch,
    ) -> None:
        from llm_code.hayhooks.debug_repl import DebugReplServer

        server = DebugReplServer(host="127.0.0.1", port=0, config=None)
        server._runtime = MagicMock()

        class _FakeCtx:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

        def fake_serve(*args, **kwargs):  # noqa: ARG001
            return _FakeCtx()

        async def short_future():
            return None

        import llm_code.hayhooks.debug_repl as mod

        class _FakeWs:
            serve = staticmethod(fake_serve)

        monkeypatch.setitem(sys.modules, "websockets", _FakeWs)
        monkeypatch.setitem(mod.__dict__, "websockets", _FakeWs)
        monkeypatch.setitem(mod.__dict__, "asyncio_Future", short_future)
        # Replace asyncio.Future() by patching it on the module's asyncio alias.
        import asyncio as _asyncio
        monkeypatch.setattr(_asyncio, "Future", short_future)

        await server.start()
        server._runtime.shutdown.assert_called_once()

    @pytest.mark.asyncio
    async def test_port_zero_reports_bound_port(
        self, monkeypatch, capsys,
    ) -> None:
        from llm_code.hayhooks.debug_repl import DebugReplServer

        server = DebugReplServer(host="127.0.0.1", port=0, config=None)

        class _FakeSocket:
            def getsockname(self):
                return ("127.0.0.1", 43210)

        class _FakeCtx:
            sockets = [_FakeSocket()]

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

        def fake_serve(*args, **kwargs):  # noqa: ARG001
            return _FakeCtx()

        async def short_future():
            return None

        import llm_code.hayhooks.debug_repl as mod

        class _FakeWs:
            serve = staticmethod(fake_serve)

        monkeypatch.setitem(sys.modules, "websockets", _FakeWs)
        monkeypatch.setitem(mod.__dict__, "websockets", _FakeWs)
        import asyncio as _asyncio
        monkeypatch.setattr(_asyncio, "Future", short_future)

        await server.start()

        out = capsys.readouterr().out
        assert "ws://127.0.0.1:43210" in out
        assert "ws://127.0.0.1:0" not in out


class TestDebugReplClient:
    def test_init_adds_ws_prefix(self):
        from llm_code.hayhooks.debug_repl import DebugReplClient

        c = DebugReplClient("localhost:9999")
        assert c._url == "ws://localhost:9999"

    def test_preserves_explicit_scheme(self):
        from llm_code.hayhooks.debug_repl import DebugReplClient

        c = DebugReplClient("ws://host:1")
        assert c._url == "ws://host:1"


class TestDebugReplFastApiMount:
    def test_health_endpoint(self):
        pytest.importorskip("fastapi")
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from llm_code.hayhooks.debug_repl import register_debug_repl_routes

        class _Cfg:
            enable_debug_repl = True

        app = FastAPI()
        register_debug_repl_routes(app, _Cfg())
        r = TestClient(app).get("/debug/repl/health")
        assert r.status_code == 200
        assert r.json()["enabled"] is True
