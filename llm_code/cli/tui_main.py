"""Entry point for llm-code."""
from __future__ import annotations

import os
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
@click.option("--serve", is_flag=True, help="Start as remote server")
@click.option("--port", type=int, default=8765, help="Server port (for --serve)")
@click.option("--connect", default=None, help="Connect to remote server (host:port)")
@click.option("--ssh", default=None, help="SSH to remote host and connect (user@host)")
@click.option("--resume", default=None, help="Resume from a checkpoint (session_id or 'last')")
def main(
    prompt: str | None,
    model: str | None,
    api: str | None,
    api_key: str | None,
    permission: str | None,
    budget: int | None,
    verbose: bool = False,
    serve: bool = False,
    port: int = 8765,
    connect: str | None = None,
    ssh: str | None = None,
    resume: str | None = None,
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

    # Resolve resume session if requested
    resume_session = None
    if resume:
        from llm_code.runtime.checkpoint_recovery import CheckpointRecovery
        checkpoints_dir = Path.home() / ".llm-code" / "checkpoints"
        recovery = CheckpointRecovery(checkpoints_dir)
        if resume == "last":
            resume_session = recovery.detect_last_checkpoint()
        else:
            resume_session = recovery.load_checkpoint(resume)
        if resume_session is None:
            print(f"[warning] No checkpoint found for: {resume}")
        else:
            print(f"Resuming session {resume_session.id} ({len(resume_session.messages)} messages)")

    # Textual fullscreen TUI (default and only UI mode)
    from llm_code.tui.app import LLMCodeTUI
    app = LLMCodeTUI(config=config, cwd=cwd, budget=budget)
    app.run()
