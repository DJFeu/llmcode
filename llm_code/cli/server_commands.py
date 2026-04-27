"""``llmcode server`` and ``llmcode connect`` CLI subcommands (v16 M9).

Adds three top-level groups:

* ``llmcode server start [--port 8080] [--host 127.0.0.1]`` — boots
  the JSON-RPC server backed by :mod:`llm_code.server.server`.
* ``llmcode server stop`` — best-effort signal to a running server
  via the PID file at ``~/.llmcode/server/server.pid``.
* ``llmcode server token grant|revoke|list`` — admin surface for
  bearer tokens. Backed by :class:`TokenStore` so revocation is
  immediate.
* ``llmcode connect <url>`` — interactive client; thin wrapper over
  :func:`llm_code.server.client.run_interactive_client`.

The legacy debug REPL (``llmcode --serve``) stays untouched.
"""
from __future__ import annotations

import asyncio
import json
import os
import signal
from pathlib import Path

import click

from llm_code.server.proto import SessionRole
from llm_code.server.server import SessionManager
from llm_code.server.tokens import TokenStore


def _server_dir() -> Path:
    return Path.home() / ".llmcode" / "server"


def _tokens_db_path() -> Path:
    return _server_dir() / "tokens.db"


def _pid_file() -> Path:
    return _server_dir() / "server.pid"


@click.group(name="server", help="Manage the formal llmcode server.")
def server_group() -> None:
    """Top-level ``llmcode server`` group."""


@server_group.command("start", help="Start the JSON-RPC server.")
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option("--port", default=8080, type=int, show_default=True)
def server_start(host: str, port: int) -> None:
    """Run the server in the foreground.

    Binds to ``host:port`` and stays running until SIGINT. The
    websocket transport is loaded lazily so the import-time surface
    stays light when only the token CLI is used.
    """
    try:
        import websockets  # type: ignore[import-untyped]  # noqa: F401
    except ImportError:
        click.echo(
            "Error: 'websockets' is not installed. "
            "Install with: pip install llmcode-cli[websocket]",
            err=True,
        )
        raise SystemExit(2)

    tokens = TokenStore(_tokens_db_path())
    manager = SessionManager(tokens=tokens)
    pid_file = _pid_file()
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(str(os.getpid()), encoding="utf-8")
    click.echo(f"llmcode server listening on ws://{host}:{port}")

    async def _run() -> None:
        from llm_code.server import websocket_transport
        await websocket_transport.serve(host=host, port=port, manager=manager)

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        pass
    finally:
        if pid_file.exists():
            try:
                pid_file.unlink()
            except OSError:
                pass


@server_group.command("stop", help="Stop a running server (SIGTERM).")
def server_stop() -> None:
    pid_file = _pid_file()
    if not pid_file.exists():
        click.echo("No PID file at ~/.llmcode/server/server.pid", err=True)
        raise SystemExit(1)
    try:
        pid = int(pid_file.read_text(encoding="utf-8").strip())
    except (OSError, ValueError) as exc:
        click.echo(f"Failed to read PID file: {exc}", err=True)
        raise SystemExit(1)
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        click.echo(f"No process found with pid {pid}; cleaning PID file", err=True)
        pid_file.unlink()
        raise SystemExit(1)
    click.echo(f"Sent SIGTERM to {pid}")


# ── token group ───────────────────────────────────────────────────────


@server_group.group("token", help="Manage bearer tokens.")
def token_group() -> None:
    """Token admin sub-group."""


@token_group.command("grant", help="Mint a token for a session.")
@click.argument("session_id")
@click.option(
    "--role",
    type=click.Choice([r.value for r in SessionRole]),
    default=SessionRole.WRITER.value,
    show_default=True,
)
@click.option("--ttl", type=int, default=3600, show_default=True)
def token_grant(session_id: str, role: str, ttl: int) -> None:
    store = TokenStore(_tokens_db_path())
    bearer = store.grant(session_id, SessionRole(role), ttl=float(ttl))
    click.echo(json.dumps({
        "token": bearer.token,
        "session_id": bearer.session_id,
        "role": bearer.role.value,
        "expires_at": bearer.expires_at,
        "fingerprint": bearer.fingerprint,
    }, indent=2))


@token_group.command("revoke", help="Revoke a token by full string.")
@click.argument("token")
def token_revoke(token: str) -> None:
    store = TokenStore(_tokens_db_path())
    if store.revoke(token):
        click.echo("revoked")
    else:
        click.echo("token not found", err=True)
        raise SystemExit(1)


@token_group.command("list", help="List all current tokens.")
def token_list() -> None:
    store = TokenStore(_tokens_db_path())
    rows = store.list_tokens()
    redacted = [
        {
            "fingerprint": r["fingerprint"],
            "session_id": r["session_id"],
            "role": r["role"],
            "expires_at": r["expires_at"],
        }
        for r in rows
    ]
    click.echo(json.dumps(redacted, indent=2))


# ── connect command ───────────────────────────────────────────────────


@click.command(name="connect", help="Connect to a running llmcode server.")
@click.argument("url")
@click.option("--token", required=True, help="Bearer token (env: LLMCODE_SERVER_TOKEN)")
@click.option(
    "--role",
    type=click.Choice([r.value for r in SessionRole]),
    default=SessionRole.WRITER.value,
    show_default=True,
)
@click.option("--session-id", default=None, help="Existing session id (otherwise create).")
def connect_command(url: str, token: str, role: str, session_id: str | None) -> None:
    from llm_code.server.client import run_interactive_client
    code = asyncio.run(
        run_interactive_client(
            url=url,
            token=token,
            role=SessionRole(role),
            session_id=session_id,
        )
    )
    if code:
        raise SystemExit(code)
