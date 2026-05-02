"""``llmcode hayhooks serve`` — standalone click group.

Kept out of ``llm_code/cli/main.py`` so the main CLI does not gain a hard
dependency on the optional ``hayhooks`` extras. ``llm_code/cli/main.py``
registers this group lazily, so importing the main CLI without FastAPI
installed still works.
"""
from __future__ import annotations

from pathlib import Path

import click

_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", ""}


@click.group(name="hayhooks")
def hayhooks_serve() -> None:
    """Expose llmcode as an MCP server / OpenAI-compat HTTP endpoint."""


def _is_loopback(host: str) -> bool:
    return host in _LOOPBACK_HOSTS


@hayhooks_serve.command(name="serve")
@click.option(
    "--transport",
    type=click.Choice(["stdio", "sse", "openai"]),
    default="stdio",
    show_default=True,
    help="Which transport to run.",
)
@click.option(
    "--host",
    default="127.0.0.1",
    show_default=True,
    help="Bind address for sse/openai transports.",
)
@click.option(
    "--port",
    type=int,
    default=8080,
    show_default=True,
    help="Bind port for sse/openai transports.",
)
@click.option(
    "--allow-remote",
    is_flag=True,
    default=False,
    help="Permit non-loopback binds (dangerous — use a reverse proxy with TLS).",
)
@click.option(
    "--config",
    "config_path",
    type=click.Path(),
    default=None,
    help="Path to a config file.",
)
def serve(
    transport: str,
    host: str,
    port: int,
    allow_remote: bool,
    config_path: str | None,
) -> None:
    """Start a hayhooks transport."""
    if not allow_remote and not _is_loopback(host):
        raise click.ClickException(
            f"refusing to bind {host!r} without --allow-remote. "
            "Hayhooks defaults to 127.0.0.1; pass --allow-remote to override."
        )

    config = _load_hayhooks_config(config_path)
    _dispatch(transport, host, port, config)


# --- helpers ---------------------------------------------------------


def _load_hayhooks_config(config_path: str | None):
    """Resolve a HayhooksConfig from disk config + CLI overrides."""
    from llm_code.runtime.config import HayhooksConfig, load_config

    cwd = Path.cwd()
    try:
        cfg = load_config(
            user_dir=Path.home() / ".llmcode",
            project_dir=cwd / ".llmcode",
            local_path=(
                Path(config_path)
                if config_path
                else cwd / ".llmcode" / "config.local.json"
            ),
            cli_overrides={},
        )
    except Exception:
        # Config resolution must never block `hayhooks serve --help`.
        return HayhooksConfig()
    return getattr(cfg, "hayhooks", None) or getattr(
        getattr(cfg, "engine", None), "hayhooks", None
    ) or HayhooksConfig()


def _dispatch(transport: str, host: str, port: int, config) -> None:
    if transport == "stdio":
        from llm_code.hayhooks.mcp_transport import run_stdio
        run_stdio(config)
    elif transport == "sse":
        from llm_code.hayhooks.mcp_transport import run_sse
        run_sse(config, host=host, port=port)
    elif transport == "openai":
        from llm_code.hayhooks.openai_compat import run_openai
        run_openai(config, host=host, port=port)
    else:  # pragma: no cover — click enforces the choice
        raise click.ClickException(f"unknown transport: {transport}")
