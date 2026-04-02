"""Remote client — connects to a remote llm-code server, renders UI locally."""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

import websockets

from rich.console import Console
from rich.markdown import Markdown
from rich.text import Text

console = Console()


class RemoteClient:
    def __init__(self, url: str):
        self._url = url if url.startswith("ws") else f"ws://{url}"
        self._ws = None

    async def connect(self) -> None:
        """Connect to remote server and start UI."""
        console.print(f"[dim]Connecting to {self._url}...[/]")

        try:
            async with websockets.connect(self._url) as ws:
                self._ws = ws
                console.print(f"[green]✓ Connected[/]")

                # Start reading server events in background
                recv_task = asyncio.create_task(self._recv_loop(ws))

                # Input loop
                from prompt_toolkit import PromptSession
                from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
                session = PromptSession(auto_suggest=AutoSuggestFromHistory())

                while True:
                    try:
                        user_input = await session.prompt_async("❯ ")
                    except (EOFError, KeyboardInterrupt):
                        console.print("\n[dim]Disconnecting...[/]")
                        break

                    user_input = user_input.strip()
                    if not user_input:
                        continue

                    if user_input in ("/exit", "/quit"):
                        break

                    await ws.send(json.dumps({"type": "user_input", "text": user_input}))

                recv_task.cancel()

        except ConnectionRefusedError:
            console.print(f"[red]Cannot connect to {self._url}[/]")
        except Exception as exc:
            console.print(f"[red]Connection error: {exc}[/]")

    async def _recv_loop(self, ws) -> None:
        """Receive and render server events."""
        try:
            async for raw in ws:
                msg = json.loads(raw)
                self._render_event(msg)
        except asyncio.CancelledError:
            pass
        except websockets.ConnectionClosed:
            console.print("[dim]Server disconnected.[/]")

    def _render_event(self, msg: dict) -> None:
        """Render a server event — same format as Ink IPC protocol."""
        msg_type = msg.get("type", "")

        if msg_type == "welcome":
            console.print()
            console.print(f"  [bold cyan]╭──────────────╮[/]")
            console.print(f"  [bold cyan]│   llm-code   │[/]  [dim](remote)[/]")
            console.print(f"  [bold cyan]╰──────────────╯[/]")
            console.print(f"  [yellow]Model         [/] {msg.get('model', '')}")
            console.print(f"  [yellow]Directory     [/] {msg.get('cwd', '')}")
            console.print(f"  [yellow]Server        [/] {self._url}")
            console.print()

        elif msg_type == "user_echo":
            console.print(f"\n[bold]❯[/] {msg.get('text', '')}")

        elif msg_type == "thinking_start":
            console.print("[blue]⠋ Thinking…[/]", end="\r")

        elif msg_type == "thinking_stop":
            elapsed = msg.get("elapsed", 0)
            console.print(f"[dim]({elapsed:.1f}s)[/]        ")

        elif msg_type == "text_delta":
            text = msg.get("text", "")
            if text.strip():
                console.print(Markdown(text, code_theme="monokai"))

        elif msg_type == "text_done":
            text = msg.get("text", "")
            if text.strip():
                console.print(Markdown(text, code_theme="monokai"))

        elif msg_type == "tool_start":
            name = msg.get("name", "")
            detail = msg.get("detail", "")
            console.print(f"\n  [grey62]╭─[/] [bold cyan]{name}[/] [grey62]─╮[/]")
            console.print(f"  [grey62]│[/] {detail}")
            console.print(f"  [grey62]╰{'─' * (len(name) + 4)}╯[/]")

        elif msg_type == "tool_result":
            output = msg.get("output", "")
            is_error = msg.get("isError", False)
            if is_error:
                console.print(f"  [bold red]✗[/] {output[:150]}")
            else:
                lines = output.strip().splitlines()[:3]
                for line in lines:
                    console.print(f"  [green]✓[/] [dim]{line[:150]}[/]")

        elif msg_type == "turn_done":
            elapsed = msg.get("elapsed", 0)
            tokens = msg.get("tokens", 0)
            console.print(f"[green]✓ Done ({elapsed:.1f}s)[/]  [dim]↓{tokens} tok[/]")
            console.print()

        elif msg_type == "message":
            console.print(f"[dim]{msg.get('text', '')}[/]")

        elif msg_type == "error":
            console.print(f"[bold red]Error: {msg.get('message', '')}[/]")

        elif msg_type == "help":
            for c in msg.get("commands", []):
                console.print(f"  [dim]{c['cmd']:<20} {c['desc']}[/]")
