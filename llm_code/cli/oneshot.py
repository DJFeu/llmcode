"""One-shot CLI modes: -x (execute shell) and -q (quick answer)."""
from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path

from llm_code.api.client import ProviderClient
from llm_code.api.types import (
    Message,
    MessageRequest,
    MessageResponse,
    TextBlock,
)
from llm_code.runtime.config import RuntimeConfig
from llm_code.runtime.model_aliases import resolve_model
from llm_code.view.dialog_types import Choice, DialogCancelled
from llm_code.view.headless import HeadlessDialogs


def _extract_text(response: MessageResponse) -> str:
    """Extract concatenated text from a MessageResponse."""
    parts: list[str] = []
    for block in response.content:
        if isinstance(block, TextBlock):
            parts.append(block.text)
    return "".join(parts).strip()


def _create_provider(config: RuntimeConfig) -> ProviderClient:
    """Build an LLMProvider from RuntimeConfig.

    v16 M6 — auth registry supplies the API key when the configured
    env var is unset; env var still wins for explicit overrides.
    """
    from llm_code.runtime.auth import resolve_api_key

    api_key = resolve_api_key(config.provider_api_key_env)
    base_url = config.provider_base_url or ""
    resolved_model = resolve_model(
        config.model, custom_aliases=config.model_aliases,
    )
    return ProviderClient.from_model(
        model=resolved_model,
        base_url=base_url,
        api_key=api_key,
        timeout=config.timeout,
        max_retries=config.max_retries,
        native_tools=False,
    )


def _send_sync(
    config: RuntimeConfig,
    user_text: str,
    system: str | None = None,
) -> str:
    """Send a single user message and return the text response."""
    provider = _create_provider(config)
    resolved_model = resolve_model(
        config.model, custom_aliases=config.model_aliases,
    )
    request = MessageRequest(
        model=resolved_model,
        messages=(
            Message(role="user", content=(TextBlock(text=user_text),)),
        ),
        system=system,
        stream=False,
    )
    response = asyncio.run(provider.send_message(request))
    return _extract_text(response)


def run_execute_mode(prompt: str, config: RuntimeConfig) -> None:
    """Translate natural language to shell command, confirm, then execute.

    Args:
        prompt: Natural language description of desired shell command.
        config: Loaded runtime config.
    """
    system_msg = (
        "You are a shell command translator. Given a natural language request, "
        "output ONLY the shell command that accomplishes it. No explanation, "
        "no markdown, no code fences. Just the raw command."
    )

    command = _send_sync(config, prompt, system=system_msg)

    # Display and confirm via Dialogs Protocol
    print(f"\033[1;36m→\033[0m {command}")

    dialogs = HeadlessDialogs()

    try:
        action = asyncio.run(dialogs.select(
            "Execute?",
            [
                Choice("y", "Yes — run the command"),
                Choice("n", "No — cancel"),
                Choice("e", "Edit — modify before running"),
            ],
            default="n",
        ))
    except (DialogCancelled, EOFError, KeyboardInterrupt):
        print("\nCancelled.")
        return

    if action == "e":
        try:
            command = asyncio.run(dialogs.text(
                "Command",
                default=command,
            ))
            if not command.strip():
                print("Cancelled.")
                return
        except (DialogCancelled, EOFError, KeyboardInterrupt):
            print("\nCancelled.")
            return
        action = "y"

    if action == "y":
        result = subprocess.run(command, shell=True, cwd=Path.cwd())
        sys.exit(result.returncode)
    else:
        print("Cancelled.")


def run_quick_mode(
    prompt: str,
    config: RuntimeConfig,
    stdin_text: str | None = None,
    *,
    output_format: str = "text",
    headless: bool = False,
) -> int:
    """Quick Q&A — send prompt through the full ConversationRuntime and
    print the visible text to stdout.

    v16 M8: ``output_format='json'`` + ``headless=True`` produce a
    structured stdout payload with exit codes for CI. Schema:

        {
          "output": "...",
          "tool_calls": [{"name": "...", "args": {...}}, ...],
          "tokens": {"input": int, "output": int},
          "exit_code": int,
          "error": "..." | null
        }

    Exit codes (returned as int, raised as SystemExit by the CLI
    wrapper):

    * 0 — success
    * 1 — tool error
    * 2 — model / provider error
    * 3 — auth error (provider key missing or invalid)
    * 4 — user cancel (Ctrl-C)

    Before 2026-04-08 this function called the provider directly,
    bypassing the system prompt, the tool registry, the tool-call
    parser, and the renderer. That meant ``-q`` was useless as a smoke
    test for the main code path, and PRs #11/#13/#14 each shipped bugs
    that ``-q`` verification missed.

    Args:
        prompt: The question or instruction.
        config: Loaded runtime config.
        stdin_text: Optional text piped via stdin.
        output_format: ``"text"`` (default) or ``"json"``.
        headless: When True, errors yield JSON-shaped output with a
            non-zero exit code instead of raising.

    Returns:
        Exit code suitable for ``raise SystemExit``. ``0`` on success.
    """
    full_prompt = prompt
    if stdin_text:
        full_prompt = f"{prompt}\n\n```\n{stdin_text}\n```"

    from llm_code.api.errors import ProviderAuthError, ProviderError
    from llm_code.api.types import (
        StreamTextDelta,
        StreamToolExecStart,
        StreamToolUseStart,
    )
    from llm_code.runtime.context import ProjectContext
    from llm_code.runtime.conversation import ConversationRuntime
    from llm_code.runtime.core_tools import register_core_tools
    from llm_code.runtime.permissions import PermissionMode, PermissionPolicy
    from llm_code.runtime.prompt import SystemPromptBuilder
    from llm_code.runtime.session import Session
    from llm_code.tools.registry import ToolRegistry

    provider = _create_provider(config)
    registry = ToolRegistry()

    # F5-wire-3: session-scoped lifecycle manager collects every
    # sandbox backend handed to a tool so we can close them all in
    # the finally block below.
    from llm_code.sandbox.lifecycle import SandboxLifecycleManager
    lifecycle = SandboxLifecycleManager()

    # Register the same collaborator-free core tool set as the REPL boot
    # path for parity between one-shot and interactive modes.
    register_core_tools(registry, config, lifecycle=lifecycle)

    cwd = Path.cwd()
    mode_map = {
        "read_only": PermissionMode.READ_ONLY,
        "workspace_write": PermissionMode.WORKSPACE_WRITE,
        "full_access": PermissionMode.FULL_ACCESS,
        "auto_accept": PermissionMode.AUTO_ACCEPT,
        "prompt": PermissionMode.PROMPT,
    }
    perm_mode = mode_map.get(
        getattr(config, "permission_mode", "prompt"), PermissionMode.PROMPT
    )

    runtime = ConversationRuntime(
        provider=provider,
        tool_registry=registry,
        permission_policy=PermissionPolicy(mode=perm_mode),
        hook_runner=None,
        prompt_builder=SystemPromptBuilder(),
        config=config,
        session=Session.create(project_path=cwd),
        context=ProjectContext(
            cwd=cwd, is_git_repo=False, git_status="", instructions=""
        ),
    )
    # F5-wire-3: hand the manager populated above to the runtime so
    # ``runtime.shutdown()`` closes every registered backend.
    runtime._sandbox_lifecycle = lifecycle

    tool_calls: list[dict] = []

    def _record_tool_call(
        *,
        name: str,
        tool_id: str = "",
        args_summary: str | None = None,
    ) -> None:
        payload = {"name": name, "id": tool_id}
        if args_summary is not None:
            payload["args_summary"] = args_summary
        if tool_id:
            for existing in tool_calls:
                if existing.get("id") == tool_id:
                    existing.update(payload)
                    return
        tool_calls.append(payload)

    async def _drive() -> str:
        events = await runtime.run_one_turn(full_prompt)
        parts: list[str] = []
        from llm_code.view.stream_parser import StreamEventKind, StreamParser

        profile = getattr(runtime, "_model_profile", None)
        parser = StreamParser(
            implicit_thinking=getattr(profile, "implicit_thinking", False),
            known_tool_names=frozenset(t.name for t in registry.all_tools()),
        )

        def _append_visible_text(text: str) -> None:
            for parsed_ev in parser.feed(text):
                if parsed_ev.kind == StreamEventKind.TEXT:
                    parts.append(parsed_ev.text)

        for ev in events:
            if isinstance(ev, StreamTextDelta):
                _append_visible_text(ev.text)
            elif isinstance(ev, StreamToolUseStart):
                _record_tool_call(
                    name=getattr(ev, "name", ""),
                    tool_id=getattr(ev, "id", ""),
                )
            elif isinstance(ev, StreamToolExecStart):
                _record_tool_call(
                    name=getattr(ev, "tool_name", ""),
                    tool_id=getattr(ev, "tool_id", ""),
                    args_summary=getattr(ev, "args_summary", ""),
                )
        for parsed_ev in parser.flush():
            if parsed_ev.kind == StreamEventKind.TEXT:
                parts.append(parsed_ev.text)
        return "".join(parts)

    visible = ""
    error_message: str | None = None
    exit_code = 0
    try:
        visible = asyncio.run(_drive())
    except KeyboardInterrupt:
        exit_code = 4
        error_message = "User cancel"
    except ProviderAuthError as exc:
        exit_code = 3
        error_message = f"Auth error: {exc}"
    except ProviderError as exc:
        exit_code = 2
        error_message = f"Provider error: {exc}"
    except Exception as exc:  # noqa: BLE001
        exit_code = 1
        error_message = f"Tool error: {exc}"
    finally:
        # F5-wire-3: close any sandbox backend this one-shot opened.
        # No-op when nothing was registered (close_all is defensive).
        runtime.shutdown()

    if output_format == "json":
        import json as _json

        cost = getattr(runtime, "_cost_tracker", None)
        tokens = {
            "input": getattr(cost, "total_input_tokens", 0) if cost else 0,
            "output": getattr(cost, "total_output_tokens", 0) if cost else 0,
        }
        payload = {
            "output": visible,
            "tool_calls": tool_calls,
            "tokens": tokens,
            "exit_code": exit_code,
            "error": error_message,
        }
        sys.stdout.write(_json.dumps(payload))
        sys.stdout.write("\n")
        sys.stdout.flush()
    else:
        if visible:
            print(visible)
        elif error_message:
            print(error_message, file=sys.stderr)

    return exit_code if headless else 0
