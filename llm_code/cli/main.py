"""Entry point for llm-code — v2.0.0 REPL edition.

M11 cutover replaces the previous ``cli/tui_main.py`` Textual launcher
with this REPL-backed entry point. The CLI surface (options, one-shot
modes, remote modes, Ollama probe) is unchanged; only the interactive
path differs: instead of ``LLMCodeTUI(...).run(mouse=...)`` it builds
the AppState + REPLBackend + ViewStreamRenderer + CommandDispatcher
quartet M10 produced and runs the REPL backend's event loop.

Wiring (M11 Task 11.2, per M11-M14 audit §H2 fix):

    state      = AppState.from_config(config, cwd=cwd, budget=budget,
                                      initial_mode=cli_mode)
    backend    = REPLBackend(config=config)
    renderer   = ViewStreamRenderer(view=backend, state=state)
    dispatcher = CommandDispatcher(view=backend, state=state,
                                   renderer=renderer)
    backend.set_input_handler(dispatcher.run_turn)
    asyncio.run(backend.run())

No ``runtime.on_status_change`` call (the v1 plan's invented method):
``ViewStreamRenderer`` pushes status updates directly via
``view.update_status`` during its stream loop.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import click

_PERMISSION_CHOICES = [
    "prompt", "auto_accept", "read_only", "workspace_write", "full_access",
]


@click.command()
@click.argument("prompt", required=False)
@click.option("--model", "-m", default=None, help="Model name to use")
@click.option("--api", default=None, help="API base URL")
@click.option(
    "--api-key", default=None,
    help="API key (or set LLM_API_KEY env var)",
)
@click.option(
    "--provider", type=click.Choice(["ollama"]), default=None,
    help="LLM provider shortcut",
)
@click.option(
    "--permission",
    type=click.Choice(_PERMISSION_CHOICES),
    default=None,
    help="Permission mode",
)
@click.option(
    "--budget", type=int, default=None, help="Token budget target",
)
@click.option(
    "--verbose", "-v", is_flag=True, help="Enable verbose logging",
)
@click.option(
    "--log-file",
    "log_file",
    default=None,
    help=(
        "Write verbose logs to PATH instead of stderr. Also reads "
        "LLMCODE_LOG_FILE env var."
    ),
)
@click.option("--serve", is_flag=True, help="Start as remote server")
@click.option(
    "--port", type=int, default=8765, help="Server port (for --serve)",
)
@click.option(
    "--connect", default=None,
    help="Connect to remote server (host:port)",
)
@click.option(
    "--ssh", default=None,
    help="SSH to remote host and connect (user@host)",
)
@click.option(
    "--replay", default=None, help="Replay a VCR recording file (.jsonl)",
)
@click.option(
    "--replay-speed", type=float, default=1.0,
    help="Playback speed for --replay (0 = instant)",
)
@click.option(
    "--resume", default=None,
    help="Resume from a checkpoint (session_id or 'last')",
)
@click.option(
    "--mode",
    "cli_mode",
    type=click.Choice(["suggest", "normal", "plan"]),
    default=None,
    help="Interaction mode (suggest/normal/plan)",
)
@click.option(
    "--yolo", is_flag=True, default=False,
    help="YOLO mode: auto-accept all permissions (dangerous)",
)
@click.option(
    "-x", "--execute", "execute_prompt", default=None,
    help="Translate to shell command and execute",
)
@click.option(
    "-q", "--quick", "quick_prompt", default=None,
    help="Quick Q&A (headless)",
)
@click.option(
    "--config-schema", is_flag=True, default=False,
    help="Print the ConfigSchema JSON schema and exit",
)
@click.option(
    "--preset", default=None,
    help=(
        "Load a built-in config preset (local-qwen, claude-cloud, "
        "mixed-routing, cost-saving)"
    ),
)
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
    cli_mode: str | None = None,
    yolo: bool = False,
    execute_prompt: str | None = None,
    quick_prompt: str | None = None,
    config_schema: bool = False,
    preset: str | None = None,
    log_file: str | None = None,
) -> None:
    """llm-code: AI coding assistant CLI."""
    from llm_code.logging import setup_logging
    from llm_code.runtime.config import load_config

    if config_schema:
        import json as _json

        from llm_code.runtime.config import ConfigSchema
        click.echo(_json.dumps(ConfigSchema.model_json_schema(), indent=2))
        return

    setup_logging(verbose=verbose, log_file=log_file)
    cwd = Path.cwd()

    # Build CLI overrides layered on top of the loaded config.
    cli_overrides: dict = {}
    if preset:
        from llm_code.runtime.config_presets import load_preset
        preset_data = load_preset(preset)
        if preset_data is None:
            click.echo(f"Error: unknown preset '{preset}'", err=True)
            raise SystemExit(2)
        cli_overrides.update(preset_data)
    if model:
        cli_overrides["model"] = model
    if api:
        cli_overrides.setdefault("provider", {})["base_url"] = api
    if api_key:
        os.environ["LLM_API_KEY"] = api_key
    if permission:
        cli_overrides.setdefault("permissions", {})["mode"] = permission

    _MODE_PERMISSION_MAP = {
        "suggest": "prompt",
        "normal": "workspace_write",
        "plan": "prompt",
    }
    if cli_mode:
        cli_overrides.setdefault("permissions", {})["mode"] = (
            _MODE_PERMISSION_MAP[cli_mode]
        )
    if yolo:
        cli_overrides.setdefault("permissions", {})["mode"] = "auto_accept"

    # Ollama provider auto-setup
    if provider == "ollama":
        ollama_result = _run_ollama_setup(
            api_override=api, model_override=model,
        )
        if ollama_result is None:
            click.echo(
                "Error: Cannot connect to Ollama at localhost:11434", err=True,
            )
            click.echo(
                "Make sure Ollama is running: ollama serve", err=True,
            )
            raise SystemExit(1)
        selected_model, base_url = ollama_result
        cli_overrides["model"] = selected_model
        cli_overrides.setdefault("provider", {})["base_url"] = base_url

    user_dir = Path.home() / ".llmcode"
    config = load_config(
        user_dir=user_dir,
        project_dir=cwd,
        local_path=cwd / ".llmcode" / "config.json",
        cli_overrides=cli_overrides,
    )

    # === One-shot modes (skip the REPL) ===

    if execute_prompt:
        from llm_code.cli.oneshot import run_execute_mode
        run_execute_mode(execute_prompt, config)
        return

    if quick_prompt:
        stdin_text = None
        if not sys.stdin.isatty():
            stdin_text = sys.stdin.read()
        from llm_code.cli.oneshot import run_quick_mode
        run_quick_mode(quick_prompt, config, stdin_text)
        return

    import asyncio

    if replay:
        from llm_code.runtime.vcr import VCRPlayer
        player = VCRPlayer(Path(replay))
        summary = player.summary()
        print(f"Replaying: {replay}")
        print(
            f"  events={summary['event_count']}  "
            f"duration={summary['duration']:.1f}s"
        )
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
        remote_client = RemoteClient(connect)
        asyncio.run(remote_client.connect())
        return

    if ssh:
        from llm_code.remote.ssh_proxy import ssh_connect
        asyncio.run(ssh_connect(ssh, port=port))
        return

    # --resume handling — we print a hint line for feature parity with
    # v1.x; the REPL backend's /checkpoint resume command can reload a
    # specific checkpoint interactively once the session is up.
    if resume:
        from llm_code.runtime.checkpoint_recovery import CheckpointRecovery
        checkpoints_dir = Path.home() / ".llmcode" / "checkpoints"
        recovery = CheckpointRecovery(checkpoints_dir)
        if resume == "last":
            resume_session = recovery.detect_last_checkpoint()
        else:
            resume_session = recovery.load_checkpoint(resume)
        if resume_session is None:
            print(f"[warning] No checkpoint found for: {resume}")
        else:
            print(
                f"Resuming session {resume_session.id} "
                f"({len(resume_session.messages)} messages)"
            )

    # === Interactive REPL ===

    from llm_code.runtime.app_state import AppState
    from llm_code.view.dispatcher import CommandDispatcher
    from llm_code.view.repl.backend import REPLBackend
    from llm_code.view.stream_renderer import ViewStreamRenderer

    state = AppState.from_config(
        config,
        cwd=cwd,
        budget=budget,
        initial_mode=cli_mode or "workspace_write",
    )
    backend = REPLBackend(
        config=config,
        runtime=state.runtime,
    )
    renderer = ViewStreamRenderer(view=backend, state=state)
    dispatcher = CommandDispatcher(
        view=backend, state=state, renderer=renderer,
    )

    # LLMCODE_TEST_MODE=1 makes every plain-text submission echo as
    # `echo: {text}` instead of calling the real LLM. Slash commands,
    # custom commands, and skill commands still go through the real
    # dispatcher path so pexpect smoke tests can exercise `/version`,
    # `/help`, `/quit`, etc. without an API key or network.
    #
    # Added in M12 as the audit §H3 fix: the M11-M14 audit discovered
    # that the M12 plan template referenced this env var but no code
    # actually honored it.
    if os.environ.get("LLMCODE_TEST_MODE") == "1":
        real_run_turn = dispatcher.run_turn

        async def _test_mode_run_turn(text: str, images=None) -> None:
            stripped = text.strip()
            if stripped.startswith("/"):
                await real_run_turn(text, images=images)
                return
            if stripped:
                backend.print_info(f"echo: {text}")

        backend.set_input_handler(_test_mode_run_turn)
    else:
        backend.set_input_handler(dispatcher.run_turn)

    asyncio.run(_run_repl(backend))


async def _run_repl(backend) -> None:
    """Start + run + stop the REPL backend with proper lifecycle.

    Kept as a coroutine so ``asyncio.run`` only appears once in the
    main(), and so a future wrapper (profiling, VCR capture, etc.) can
    sit between the entry point and the backend lifecycle without
    touching main().
    """
    await backend.start()
    try:
        await backend.run()
    finally:
        await backend.stop()


_OLLAMA_DEFAULT_URL = "http://localhost:11434"


def _run_ollama_setup(
    api_override: str | None = None,
    model_override: str | None = None,
) -> tuple[str, str] | None:
    """Probe Ollama, optionally select model.

    Returns ``(model, base_url)`` or ``None`` on failure.
    """
    import asyncio as _asyncio

    base_url = api_override or _OLLAMA_DEFAULT_URL

    async def _setup() -> tuple[str, str] | None:
        from llm_code.runtime.hardware import detect_vram_gb
        from llm_code.runtime.ollama import (
            OllamaClient, sort_models_for_selection,
        )

        client = OllamaClient(base_url=base_url)
        try:
            if not await client.probe():
                return None

            if model_override:
                return (model_override, f"{base_url}/v1")

            models = await client.list_models()
            if not models:
                click.echo(
                    "No models found in Ollama. Download one first:",
                    err=True,
                )
                click.echo("  ollama pull qwen3:1.7b", err=True)
                return None

            if len(models) == 1:
                click.echo(f"Using Ollama model: {models[0].name}")
                return (models[0].name, f"{base_url}/v1")

            vram_gb = detect_vram_gb()
            sorted_models = sort_models_for_selection(models, vram_gb)

            from llm_code.view.dialog_types import Choice, DialogCancelled
            from llm_code.view.headless import HeadlessDialogs

            dialogs = HeadlessDialogs()
            choices = [
                Choice(
                    value=m.name,
                    label=m.name,
                    hint=getattr(m, "size_label", None),
                )
                for m in sorted_models
            ]
            try:
                selected_name = await dialogs.select(
                    "Select model",
                    choices,
                    default=sorted_models[0].name,
                )
            except DialogCancelled:
                selected_name = sorted_models[0].name

            selected = next(
                (m for m in sorted_models if m.name == selected_name),
                sorted_models[0],
            )
            click.echo(f"Using: {selected.name}")
            return (selected.name, f"{base_url}/v1")
        finally:
            await client.close()

    return _asyncio.run(_setup())


def _format_model_list(
    models: list,
    vram_gb: float | None,
) -> str:
    """Format models as a numbered list with VRAM annotations.

    Kept for parity with the v1.x tui_main surface so the existing
    ``tests/test_cli/test_provider_ollama.py`` keeps working
    unchanged.
    """
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

        lines.append(
            f"  {prefix}{i}) {model.name:<20s} ({size_str}){suffix}"
        )

    lines.append("")
    return "\n".join(lines)


# Allow ``python -m llm_code.cli.main`` to invoke the Click command.
# Without this guard the module imports cleanly but does nothing — the
# subprocess-based swarm backends and the M11.6 smoke test both rely
# on running ``python -m llm_code.cli.main ...``, which needs a
# module-level call to the click command.
if __name__ == "__main__":
    main()
