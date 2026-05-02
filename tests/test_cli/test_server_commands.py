from __future__ import annotations

import sys
from types import SimpleNamespace

from click.testing import CliRunner

from llm_code.cli.server_commands import server_start


def test_server_start_wires_runtime_factory(monkeypatch, tmp_path) -> None:
    from llm_code.server import websocket_transport

    captured: dict[str, object] = {}

    async def fake_serve(host, port, manager, on_listen=None):  # noqa: ARG001
        captured["manager"] = manager
        if on_listen is not None:
            on_listen(host, 43210)

    monkeypatch.setitem(sys.modules, "websockets", SimpleNamespace())
    monkeypatch.setattr(
        "llm_code.cli.server_commands._tokens_db_path",
        lambda: tmp_path / "tokens.db",
    )
    monkeypatch.setattr(
        "llm_code.cli.server_commands._pid_file",
        lambda: tmp_path / "server.pid",
    )
    monkeypatch.setattr(websocket_transport, "serve", fake_serve)

    result = CliRunner().invoke(server_start, ["--port", "0"])

    assert result.exit_code == 0, result.output
    assert "ws://127.0.0.1:43210" in result.output
    manager = captured["manager"]
    assert getattr(manager, "_runtime_factory") is not None
