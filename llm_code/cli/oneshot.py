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
) -> None:
    """Quick Q&A — send prompt through the full ConversationRuntime and
    print the visible text to stdout.

    Before 2026-04-08 this function called the provider directly,
    bypassing the system prompt, the tool registry, the tool-call
    parser, and the renderer. That meant ``-q`` was useless as a smoke
    test for the main code path, and PRs #11/#13/#14 each shipped bugs
    that ``-q`` verification missed.

    Args:
        prompt: The question or instruction.
        config: Loaded runtime config.
        stdin_text: Optional text piped via stdin.
    """
    full_prompt = prompt
    if stdin_text:
        full_prompt = f"{prompt}\n\n```\n{stdin_text}\n```"

    from llm_code.api.types import StreamTextDelta
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

    async def _drive() -> str:
        events = await runtime.run_one_turn(full_prompt)
        parts: list[str] = []
        for ev in events:
            if isinstance(ev, StreamTextDelta):
                parts.append(ev.text)
        return "".join(parts)

    try:
        visible = asyncio.run(_drive())
        print(visible)
    finally:
        # F5-wire-3: close any sandbox backend this one-shot opened.
        # No-op when nothing was registered (close_all is defensive).
        runtime.shutdown()
