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
import shutil
import sys
from pathlib import Path

import click

_PERMISSION_CHOICES = [
    "prompt", "auto_accept", "read_only", "workspace_write", "full_access",
]


class ReplGroup(click.Group):
    """Custom click group that lets un-registered positional tokens fall
    through to the group callback as a ``prompt`` kwarg.

    Rationale
    ---------
    We want the top-level ``llmcode`` CLI to support three usage modes
    simultaneously:

    1. ``llmcode``                    → REPL (no args)
    2. ``llmcode "some prompt text"`` → REPL (prompt captured)
    3. ``llmcode <subcommand> ...``   → dispatch to the subcommand

    Plain ``click.Group`` treats the first non-option positional as a
    subcommand name and errors out when it does not match a registered
    command. Adding ``@click.argument("prompt")`` on the group hides
    subcommands entirely because Click consumes that positional before
    the subcommand routing step. Neither default Click behavior
    satisfies all three modes at once.

    Strategy
    --------
    Override ``parse_args`` so that *after* Click's normal option +
    positional splitting runs, we inspect ``ctx._protected_args`` (the
    slot Click reserves for the subcommand name) and decide:

    * If it matches a registered subcommand → leave Click's routing
      alone; subcommand handler takes over.
    * Otherwise → move every leftover token into
      ``ctx.params['prompt']`` as a tuple, clear the protected-args
      slot, and let Click call the group callback without dispatching
      to a subcommand.

    The group callback then sees ``prompt=("hello", "world")`` and
    ``ctx.invoked_subcommand is None``, so it runs the REPL as before.
    """

    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        rest = super().parse_args(ctx, args)

        protected = list(getattr(ctx, "_protected_args", []) or [])
        remaining = list(getattr(ctx, "args", []) or [])

        # If the first protected arg is a known subcommand name, leave
        # everything as-is so Click dispatches normally.
        if protected and protected[0] in self.commands:
            return rest

        # Otherwise treat the entire tail as a prompt tuple and clear
        # the subcommand slot so the group callback runs.
        if protected or remaining:
            ctx.params["prompt"] = tuple(protected + remaining)
            ctx._protected_args = []
            ctx.args = []
        return rest


@click.group(
    cls=ReplGroup,
    invoke_without_command=True,
    no_args_is_help=False,
)
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
    "--allow-remote",
    is_flag=True,
    help=(
        "Bind --serve to 0.0.0.0 (LAN/internet-reachable). "
        "Default is localhost-only. Use only on trusted networks."
    ),
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
    "--headless", is_flag=True, default=False,
    help=(
        "Headless mode: combine -q + --output-format json + structured "
        "exit codes (0=success, 1=tool error, 2=model error, 3=auth, "
        "4=user cancel). Designed for CI / GitHub Actions."
    ),
)
@click.option(
    "--output-format",
    "output_format",
    type=click.Choice(["text", "json"]),
    default="text",
    help=(
        "Output format for one-shot modes (-q / --headless). text = "
        "plain stdout (default), json = single JSON object suitable "
        "for piping into jq."
    ),
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
@click.pass_context
def main(
    ctx: click.Context,
    model: str | None,
    api: str | None,
    api_key: str | None,
    provider: str | None,
    permission: str | None,
    budget: int | None,
    verbose: bool = False,
    serve: bool = False,
    port: int = 8765,
    allow_remote: bool = False,
    connect: str | None = None,
    ssh: str | None = None,
    replay: str | None = None,
    replay_speed: float = 1.0,
    resume: str | None = None,
    cli_mode: str | None = None,
    yolo: bool = False,
    execute_prompt: str | None = None,
    quick_prompt: str | None = None,
    headless: bool = False,
    output_format: str = "text",
    config_schema: bool = False,
    preset: str | None = None,
    log_file: str | None = None,
    prompt: tuple[str, ...] | None = None,
) -> None:
    """llm-code: AI coding assistant CLI."""
    # When a registered subcommand like ``hayhooks`` / ``memory`` /
    # ``migrate`` / ``trace`` is invoked, the subcommand's own handler
    # takes over; the group callback must not try to initialise a
    # runtime or launch the REPL.
    if ctx.invoked_subcommand is not None:
        return

    # ``prompt`` is captured by :class:`ReplGroup` as a tuple of raw
    # tokens. Re-join with a single space so callers that want to read
    # it downstream can use it as a single string, matching the v1.x
    # behavior where ``prompt`` was a bare ``str | None``.
    _ = " ".join(prompt) if prompt else None  # noqa: F841 — captured for symmetry

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

    # v16 M8 — --headless is shorthand for "-q + --output-format json
    # + structured exit codes". When --headless is set without -q, we
    # require a positional prompt or an empty quick path; the
    # ``-q "..."`` flow stays the canonical entry point so existing
    # invocations don't change shape.
    if headless and not quick_prompt and prompt:
        quick_prompt = " ".join(prompt) if isinstance(prompt, tuple) else prompt
    if headless and output_format == "text":
        # Default --headless to JSON when the user didn't pick a format.
        output_format = "json"

    if quick_prompt:
        stdin_text = None
        if not sys.stdin.isatty():
            stdin_text = sys.stdin.read()
        from llm_code.cli.oneshot import run_quick_mode
        exit_code = run_quick_mode(
            quick_prompt, config, stdin_text,
            output_format=output_format,
            headless=headless,
        )
        if exit_code:
            raise SystemExit(exit_code)
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
        # Migrated from llm_code.remote.server.RemoteServer (M4.11).
        # v2.5.3 — default to localhost-only. ``--allow-remote`` opts in
        # to 0.0.0.0 (LAN/internet-reachable) with a stderr banner so
        # operators know the surface they just exposed. Pre-v2.5.3 this
        # bound 0.0.0.0 unconditionally — silent network exposure.
        from llm_code.hayhooks.debug_repl import DebugReplServer
        host = "0.0.0.0" if allow_remote else "127.0.0.1"
        if allow_remote:
            click.echo(
                f"⚠ --allow-remote: server is listening on 0.0.0.0:{port}. "
                "Use only on trusted networks; the debug REPL has full "
                "shell access via the remote session.",
                err=True,
            )
        server = DebugReplServer(host=host, port=port, config=config)
        asyncio.run(server.start())
        return

    if connect:
        from llm_code.hayhooks.debug_repl import DebugReplClient
        remote_client = DebugReplClient(connect)
        asyncio.run(remote_client.connect())
        return

    if ssh:
        from llm_code.hayhooks.debug_repl import ssh_connect
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

    asyncio.run(_run_repl(backend, state))


def _register_subcommands() -> None:
    """Attach the standalone click groups to ``main`` as subcommands.

    Each of the four subcommand groups — hayhooks, memory, migrate,
    trace — lives in its own module and was intentionally kept out of
    ``main.py`` so the main CLI doesn't grow an import-time dependency
    on optional extras (``hayhooks`` pulls FastAPI; ``memory`` pulls
    sentence-transformers when its migrate step runs). Registering them
    here is safe because each group's module only imports click at
    module scope — all heavy deps are lazily imported inside command
    callbacks.

    Registration runs exactly once at import time. Any import failure
    is swallowed so a partial checkout or missing optional module
    cannot break the top-level ``llmcode`` entry point.
    """
    try:
        from llm_code.hayhooks.cli import hayhooks_serve
        main.add_command(hayhooks_serve, name="hayhooks")
    except Exception:  # pragma: no cover — defensive: optional extra
        pass

    try:
        from llm_code.memory.cli import memory as memory_group
        main.add_command(memory_group, name="memory")
    except Exception:  # pragma: no cover — defensive
        pass

    try:
        from llm_code.migrate.cli import migrate_cli
        main.add_command(migrate_cli, name="migrate")
    except Exception:  # pragma: no cover — defensive
        pass

    try:
        from llm_code.engine.observability.trace_cli import cli as trace_group
        main.add_command(trace_group, name="trace")
    except Exception:  # pragma: no cover — defensive
        pass

    # v16 M9 — formal client/server API. ``llmcode server start|stop|token``
    # and ``llmcode connect``. Imported lazily and silenced on failure so
    # the optional ``[websocket]`` extra never blocks the top-level CLI.
    try:
        from llm_code.cli.server_commands import connect_command, server_group
        main.add_command(server_group, name="server")
        main.add_command(connect_command, name="connect")
    except Exception:  # pragma: no cover — defensive
        pass


_register_subcommands()


async def _run_repl(backend, state) -> None:
    """Start + run + stop the REPL backend with proper lifecycle.

    Two setup steps happen between ``start()`` and ``run()``:

    1. **Anchor PT's layout at the terminal bottom.** prompt_toolkit
       non-fullscreen mode draws its layout at the current cursor
       position. On a fresh terminal that's near the top, which
       leaves the status line + input area floating at the top of
       the viewport with a giant empty scrollback region underneath.
       We push the terminal cursor to the bottom by writing newlines
       before PT takes over, so PT's first draw lands at the bottom.
       This gives the familiar "bottom chrome" feel v1.x Textual had,
       without the costs of full-screen alt-screen mode.

    2. **Schedule the welcome banner via asyncio.create_task.**
       Printing the banner BEFORE ``run()`` races PT's initial
       layout redraw — PT clobbers lines above its anchor during
       cold start, and banner content can disappear or wrap
       incorrectly. Firing the banner ~100 ms after run_async
       enters its event loop lets it flow through the
       coordinator's ``patch_stdout`` wrapper, which PT renders
       cleanly above the anchored layout.
    """
    import asyncio
    # M15: load the user's theme overrides into the brand palette
    # BEFORE any component reads it (welcome banner, status line,
    # logo, etc). Silent on failure — a bad theme key must not
    # block startup.
    try:
        from llm_code.view.repl import style as _style
        _style.set_palette(_style.load_palette(state.config))
    except Exception:
        pass
    await backend.start()
    _push_cursor_to_bottom()

    # Prime the status line with model / cwd / branch so the bar isn't
    # rendered as ``?`` while waiting for the user's first turn.
    # ViewStreamRenderer re-pushes the full context (including
    # context_limit + plan_mode) on every turn_start; this initial
    # push just covers the idle-REPL window before turn 1.
    try:
        from llm_code.view.stream_renderer import ViewStreamRenderer
        from llm_code.view.types import StatusUpdate

        initial_cwd = str(state.cwd) if getattr(state, "cwd", None) else None
        initial_branch = (
            ViewStreamRenderer._detect_git_branch(state.cwd)
            if state.cwd is not None
            else None
        )
        initial_model = (
            getattr(state.config, "model", None)
            if state.config is not None
            else None
        )
        backend.update_status(
            StatusUpdate(
                model=initial_model,
                cwd=initial_cwd,
                branch=initial_branch,
            )
        )
    except Exception:
        # Priming is best-effort: a failure here must not block
        # REPL startup. Status will render `?` for any missing field.
        pass

    async def _welcome_after_start() -> None:
        await asyncio.sleep(0.1)
        _print_welcome(backend, state)

    welcome_task = asyncio.create_task(_welcome_after_start())
    try:
        await backend.run()
    finally:
        # F5-wire-4: close any sandbox backend the REPL opened. Runs
        # before backend.stop() so Docker containers finish their
        # own teardown (can take seconds) while prompt_toolkit is
        # still alive. Guarded so a slow / broken shutdown can't
        # abort the rest of the REPL teardown chain.
        try:
            if state.runtime is not None:
                state.runtime.shutdown()
        except Exception:
            pass  # teardown must never raise

        if not welcome_task.done():
            welcome_task.cancel()
            try:
                await welcome_task
            except (asyncio.CancelledError, Exception):
                pass
        await backend.stop()


def _print_welcome(backend, state) -> None:
    """Print the M15 welcome panel once PT's event loop is up.

    Uses the full-featured LLMCODE block-letter gradient logo + info
    grid + hint footer from :mod:`llm_code.view.repl.components.welcome`.
    The panel is rendered directly onto the coordinator's console so
    the gradient colors survive the Rich rendering pipeline (the
    older ``print_panel(content, title)`` string API can't carry per-
    char styling).
    """
    try:
        from llm_code.view.repl.components.welcome import render_welcome_panel

        version = _resolve_version()
        model = (
            getattr(state.config, "model", None)
            if state.config is not None
            else None
        ) or "(no model)"
        cwd_display = state.cwd.name or str(state.cwd)
        permission_mode = (
            getattr(state.config, "permission_mode", None)
            if state.config is not None
            else None
        )
        thinking_mode = None
        if state.config is not None:
            thinking_cfg = getattr(state.config, "thinking", None)
            if thinking_cfg is not None:
                thinking_mode = getattr(thinking_cfg, "mode", None)
        try:
            rows = shutil.get_terminal_size((80, 24)).lines
        except Exception:
            rows = 24
        panel = render_welcome_panel(
            version=version,
            model=model,
            cwd=cwd_display,
            permission_mode=permission_mode,
            thinking_mode=thinking_mode,
            terminal_rows=rows,
        )
        # Route through the coordinator's Rich console so the
        # per-char gradient spans render correctly.
        coordinator = getattr(backend, "coordinator", None)
        if coordinator is not None and getattr(coordinator, "_console", None):
            coordinator._console.print(panel)
        else:
            # Fallback: stringify and use the plain panel API.
            backend.print_panel(str(panel), title=f"llmcode v{version}")
    except Exception:
        # Welcome banner must never block startup
        pass


def _push_cursor_to_bottom() -> None:
    """Emit blank newlines so the terminal cursor lands at the bottom
    of the viewport before prompt_toolkit takes over.

    In ``full_screen=False`` mode PT draws its layout at the current
    cursor position. If that position is near the top of the terminal
    (fresh tab, empty terminal), the status line + input area sit
    near the top and the rest of the viewport is an empty hole below
    them. Printing N newlines first (where N = terminal rows −
    PT layout height − welcome panel height) moves the cursor to
    the bottom, so PT anchors its layout there and content scrolls
    up into the reserved region above.

    Falls back to 24 rows (DEC VT100 default) if
    ``shutil.get_terminal_size`` fails. Subtracts a fixed 8 lines
    for the reserved layout + welcome panel; any smaller terminals
    just end up with a shorter scrollback region.
    """
    try:
        rows = shutil.get_terminal_size((80, 24)).lines
    except Exception:
        rows = 24
    reserved = 8  # PT layout (2-3) + welcome panel (6) + breathing room
    fill = max(0, rows - reserved)
    if fill > 0:
        sys.stdout.write("\n" * fill)
        sys.stdout.flush()


def _resolve_version() -> str:
    """Best-effort version lookup.

    Prefers the installed package metadata, falls back to the
    ``pyproject.toml`` version string (for editable source checkouts
    that haven't been re-installed yet), and finally falls back to a
    hardcoded ``2.0.0``. The last fallback is the common case during
    a fresh clone + direct ``python -m llm_code.cli.main`` run.
    """
    try:
        from importlib.metadata import version as _pkg_version
        return _pkg_version("llmcode-cli")
    except Exception:
        pass
    try:
        import tomllib
        pyproject = Path(__file__).resolve().parent.parent.parent / "pyproject.toml"
        if pyproject.is_file():
            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
            return str(data.get("project", {}).get("version", "2.0.0"))
    except Exception:
        pass
    return "2.0.0"


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
