"""Entry point for llm-code TUI."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import click

_PERMISSION_CHOICES = ["prompt", "auto_accept", "read_only", "workspace_write", "full_access"]


@click.command()
@click.argument("prompt", required=False)
@click.option("--model", "-m", default=None, help="Model name to use")
@click.option("--api", default=None, help="API base URL")
@click.option("--api-key", default=None, help="API key (or set LLM_API_KEY env var)")
@click.option(
    "--permission",
    type=click.Choice(_PERMISSION_CHOICES),
    default=None,
    help="Permission mode",
)
@click.option("--budget", type=int, default=None, help="Token budget target")
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose logging")
@click.option("--ink", is_flag=True, help="Use React+Ink frontend (experimental)")
@click.option("--serve", is_flag=True, help="Start as remote server")
@click.option("--port", type=int, default=8765, help="Server port (for --serve)")
@click.option("--connect", default=None, help="Connect to remote server (host:port)")
@click.option("--ssh", default=None, help="SSH to remote host and connect (user@host)")
def main(
    prompt: str | None,
    model: str | None,
    api: str | None,
    api_key: str | None,
    permission: str | None,
    budget: int | None,
    verbose: bool = False,
    ink: bool = False,
    serve: bool = False,
    port: int = 8765,
    connect: str | None = None,
    ssh: str | None = None,
) -> None:
    """llm-code: AI coding assistant CLI."""
    from llm_code.logging import setup_logging
    from llm_code.runtime.config import load_config

    setup_logging(verbose=verbose)
    cwd = Path.cwd()

    # Build CLI overrides
    cli_overrides: dict = {}
    if model:
        cli_overrides["model"] = model
    if api:
        cli_overrides.setdefault("provider", {})["base_url"] = api
    if api_key:
        os.environ["LLM_API_KEY"] = api_key
    if permission:
        cli_overrides.setdefault("permissions", {})["mode"] = permission

    user_dir = Path.home() / ".llm-code"
    config = load_config(
        user_dir=user_dir,
        project_dir=cwd,
        local_path=cwd / ".llm-code" / "config.json",
        cli_overrides=cli_overrides,
    )

    import asyncio

    if serve:
        from llm_code.remote.server import RemoteServer
        server = RemoteServer(host="0.0.0.0", port=port, config=config)
        asyncio.run(server.start())
        return

    if connect:
        from llm_code.remote.client import RemoteClient
        client = RemoteClient(connect)
        asyncio.run(client.connect())
        return

    if ssh:
        from llm_code.remote.ssh_proxy import ssh_connect
        asyncio.run(ssh_connect(ssh, port=port))
        return

    if ink:
        from llm_code.cli.ink_bridge import InkBridge
        bridge = InkBridge(config=config, cwd=cwd, budget=budget)
        asyncio.run(bridge.start())
    else:
        from llm_code.cli.tui import LLMCodeCLI
        cli = LLMCodeCLI(config=config, cwd=cwd, budget=budget)
        asyncio.run(cli.run())
