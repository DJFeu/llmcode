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
def main(
    prompt: str | None,
    model: str | None,
    api: str | None,
    api_key: str | None,
    permission: str | None,
    budget: int | None,
    verbose: bool = False,
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
    from llm_code.cli.tui import LLMCodeCLI

    cli = LLMCodeCLI(config=config, cwd=cwd, budget=budget)
    asyncio.run(cli.run())
