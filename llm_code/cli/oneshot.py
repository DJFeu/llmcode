"""One-shot CLI modes: -x (execute shell) and -q (quick answer)."""
from __future__ import annotations

import asyncio
import os
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


def _extract_text(response: MessageResponse) -> str:
    """Extract concatenated text from a MessageResponse."""
    parts: list[str] = []
    for block in response.content:
        if isinstance(block, TextBlock):
            parts.append(block.text)
    return "".join(parts).strip()


def _create_provider(config: RuntimeConfig) -> ProviderClient:
    """Build an LLMProvider from RuntimeConfig."""
    api_key = os.environ.get(config.provider_api_key_env, "")
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

    # Display and confirm
    print(f"\033[1;36m→\033[0m {command}")

    try:
        choice = input("Execute? [y/n/e(dit)] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled.")
        return

    if choice == "e":
        try:
            command = input("Command: ").strip()
            if not command:
                print("Cancelled.")
                return
        except (EOFError, KeyboardInterrupt):
            print("\nCancelled.")
            return
        choice = "y"

    if choice == "y":
        result = subprocess.run(command, shell=True, cwd=Path.cwd())
        sys.exit(result.returncode)
    else:
        print("Cancelled.")


def run_quick_mode(
    prompt: str,
    config: RuntimeConfig,
    stdin_text: str | None = None,
) -> None:
    """Quick Q&A -- send prompt to LLM and print response to stdout.

    Args:
        prompt: The question or instruction.
        config: Loaded runtime config.
        stdin_text: Optional text piped via stdin.
    """
    full_prompt = prompt
    if stdin_text:
        full_prompt = f"{prompt}\n\n```\n{stdin_text}\n```"

    response = _send_sync(config, full_prompt)
    print(response)
