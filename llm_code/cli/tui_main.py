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
@click.option("--provider", type=click.Choice(["ollama"]), default=None, help="LLM provider shortcut")
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
@click.option("--replay", default=None, help="Replay a VCR recording file (.jsonl)")
@click.option("--replay-speed", type=float, default=1.0, help="Playback speed for --replay (0 = instant)")
@click.option("--resume", default=None, help="Resume from a checkpoint (session_id or 'last')")
def main(
    prompt: str | None,
    model: str | None,
    api: str | None,
    api_key: str | None,
    provider: str | None,
    permission: str | None,
    budget: int | None,
    verbose: bool = False,
    serve: bool = False,
    port: int = 8765,
    connect: str | None = None,
    ssh: str | None = None,
    replay: str | None = None,
    replay_speed: float = 1.0,
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

    # Ollama provider setup
    if provider == "ollama":
        ollama_result = _run_ollama_setup(
            api_override=api,
            model_override=model,
        )
        if ollama_result is None:
            click.echo("Error: Cannot connect to Ollama at localhost:11434", err=True)
            click.echo("Make sure Ollama is running: ollama serve", err=True)
            raise SystemExit(1)
        selected_model, base_url = ollama_result
        cli_overrides["model"] = selected_model
        cli_overrides.setdefault("provider", {})["base_url"] = base_url

    user_dir = Path.home() / ".llm-code"
    config = load_config(
        user_dir=user_dir,
        project_dir=cwd,
        local_path=cwd / ".llm-code" / "config.json",
        cli_overrides=cli_overrides,
    )

    import asyncio

    if replay:
        from llm_code.runtime.vcr import VCRPlayer
        player = VCRPlayer(Path(replay))
        summary = player.summary()
        print(f"Replaying: {replay}")
        print(f"  events={summary['event_count']}  duration={summary['duration']:.1f}s")
        print()
        for event in player.replay(speed=replay_speed):
            print(f"[{event.type:15s}] {event.data}")
        return

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


_OLLAMA_DEFAULT_URL = "http://localhost:11434"


def _run_ollama_setup(
    api_override: str | None = None,
    model_override: str | None = None,
) -> tuple[str, str] | None:
    """Probe Ollama, optionally select model. Returns (model, base_url) or None."""
    import asyncio as _asyncio

    base_url = api_override or _OLLAMA_DEFAULT_URL

    async def _setup() -> tuple[str, str] | None:
        from llm_code.runtime.ollama import OllamaClient, sort_models_for_selection
        from llm_code.runtime.hardware import detect_vram_gb

        client = OllamaClient(base_url=base_url)
        try:
            if not await client.probe():
                return None

            if model_override:
                return (model_override, f"{base_url}/v1")

            models = await client.list_models()
            if not models:
                click.echo("No models found in Ollama. Download one first:", err=True)
                click.echo("  ollama pull qwen3:1.7b", err=True)
                return None

            if len(models) == 1:
                click.echo(f"Using Ollama model: {models[0].name}")
                return (models[0].name, f"{base_url}/v1")

            vram_gb = detect_vram_gb()
            sorted_models = sort_models_for_selection(models, vram_gb)
            output = _format_model_list(sorted_models, vram_gb)
            click.echo(output)

            choice = click.prompt("Select model", default="1")
            try:
                idx = int(choice) - 1
                if 0 <= idx < len(sorted_models):
                    selected = sorted_models[idx]
                else:
                    selected = sorted_models[0]
            except ValueError:
                selected = sorted_models[0]

            click.echo(f"Using: {selected.name}")
            return (selected.name, f"{base_url}/v1")
        finally:
            await client.close()

    return _asyncio.run(_setup())


def _format_model_list(
    models: list,
    vram_gb: float | None,
) -> str:
    """Format models as a numbered list with VRAM annotations."""
    lines = ["\nAvailable Ollama models:\n"]

    for i, model in enumerate(models, 1):
        size_str = f"~{model.estimated_vram_gb:.0f}GB"
        prefix = "  "
        suffix = ""

        if vram_gb is not None:
            if model.is_recommended(vram_gb):
                prefix = "★ "
                suffix = "  Recommended"
            elif not model.fits_in_vram(vram_gb):
                suffix = " ⚠️ May exceed available VRAM"

        lines.append(f"  {prefix}{i}) {model.name:<20s} ({size_str}){suffix}")

    lines.append("")
    return "\n".join(lines)
