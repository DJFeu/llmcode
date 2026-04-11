"""PoC — validate Rich Live + prompt_toolkit Application coexistence.

Run: /Users/adamhong/miniconda3/bin/python3 experiments/repl_poc.py

Expected behavior:
- Intro messages print to scrollback (native terminal scrollback)
- Bottom of terminal shows a reverse-video status line + 3-line input area
- Type text, press Enter -> input echoes as `> {text}` to scrollback
- Type 'stream' + Enter -> Rich Live region appears above the status line
  rendering a fake streaming Markdown response (with code block),
  then commits to scrollback when done
- Scroll wheel moves terminal scrollback natively (not captured by app)
- Mouse drag-select copy works natively (not captured by app)
- Ctrl+D on empty input exits cleanly
- Terminal resize during run adapts without garbage characters

Gate: this PoC must work in Warp, iTerm2, and tmux before the spec
(docs/superpowers/specs/2026-04-11-llm-code-repl-mode-design.md)
proceeds to M1.
"""
from __future__ import annotations

import asyncio
import sys

from prompt_toolkit import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import HSplit, Layout, Window
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.styles import Style
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel


# Fake state held at module level for simplicity. In production, all of
# this lives inside REPLBackend + ScreenCoordinator (see spec section 6.1).
_STATE = {
    "model": "Q3.5-122B",
    "project": "llmcode-poc",
    "tokens": 0,
    "cost_usd": 0.0,
}


def _status_line_text() -> str:
    """Render the status line as a reverse-video one-liner.

    Production uses StatusLine component (spec section 6.2); PoC fakes it inline.
    """
    return (
        f" {_STATE['model']} · {_STATE['project']} · "
        f"{_STATE['tokens']} tok · ${_STATE['cost_usd']:.2f} "
    )


async def _fake_stream(console: Console) -> None:
    """Emit a fake streaming Markdown response via Rich Live.

    This is the core PoC: can Rich Live refresh in place ABOVE the
    prompt_toolkit reserved area, and commit to scrollback without
    overlapping the status line or input buffer?
    """
    chunks = [
        "# Streaming test\n\n",
        "This is a **streaming Markdown** response rendered in a ",
        "Rich `Live` region above the input area.\n\n",
        "It should appear to type character-by-character, then commit ",
        "to scrollback as a clean final render.\n\n",
        "```python\n",
        "def hello(name: str) -> str:\n",
        "    return f'Hello, {name}!'\n",
        "```\n\n",
        "The code block above should be syntax-highlighted in the ",
        "final commit (though it may appear as plain text mid-stream ",
        "until the closing ``` arrives — this flicker is acceptable).",
    ]
    buffer = ""

    with Live(
        Panel(
            Markdown(buffer + "▋"),
            border_style="cyan",
            title="[dim]assistant[/dim]",
            title_align="left",
        ),
        console=console,
        refresh_per_second=10,
        transient=True,       # region clears itself on stop
        auto_refresh=True,
    ) as live:
        for chunk in chunks:
            await asyncio.sleep(0.15)
            buffer += chunk
            live.update(
                Panel(
                    Markdown(buffer + "▋"),
                    border_style="cyan",
                    title="[dim]assistant[/dim]",
                    title_align="left",
                )
            )

    # After the Live region stops (transient=True clears it), print the
    # final rendered Markdown to scrollback as permanent output.
    console.print(Markdown(buffer))

    # Update fake state
    _STATE["tokens"] += len(buffer.split())
    _STATE["cost_usd"] += 0.001


async def main() -> None:
    console = Console()

    # Intro — print to normal scrollback before the app takes the bottom
    console.print("[bold cyan]M0 PoC — REPL architecture validation[/bold cyan]")
    console.print(
        "[dim]Type anything and press Enter to echo. "
        "Type 'stream' to see a fake streaming response. "
        "Ctrl+D to exit.[/dim]"
    )
    console.print()

    input_buffer = Buffer(multiline=True)
    kb = KeyBindings()

    @kb.add("c-d")
    def _exit(event) -> None:  # type: ignore[no-untyped-def]
        event.app.exit()

    @kb.add("c-c")
    def _interrupt(event) -> None:  # type: ignore[no-untyped-def]
        # Ctrl+C clears input; second Ctrl+C on empty input exits
        if input_buffer.text:
            input_buffer.reset()
        else:
            event.app.exit()

    @kb.add("enter")
    def _submit(event) -> None:  # type: ignore[no-untyped-def]
        text = input_buffer.text.strip()
        if not text:
            return
        input_buffer.reset()
        # Schedule the async handler without blocking the key handler
        asyncio.create_task(_handle_submit(text, event.app, console))

    async def _handle_submit(text: str, app, console: Console) -> None:
        # Echo the user message to scrollback
        console.print(f"[bold green]> {text}[/bold green]")

        if text == "stream":
            await _fake_stream(console)
        elif text in {"quit", "exit", "/quit", "/exit"}:
            app.exit()

        # Trigger a redraw to refresh the status line
        app.invalidate()

    # Layout: status line (1 row, reverse video) + input area (3 rows)
    status_window = Window(
        FormattedTextControl(lambda: _status_line_text()),
        height=1,
        style="class:status",
    )
    input_window = Window(
        BufferControl(buffer=input_buffer),
        height=3,
        style="class:input",
    )

    layout = Layout(HSplit([status_window, input_window]))

    style = Style.from_dict({
        "status": "reverse",
        "input": "",
    })

    app = Application(
        layout=layout,
        key_bindings=kb,
        full_screen=False,          # KEY: don't enter alt-screen mode
        mouse_support=False,        # KEY: don't capture mouse events
        style=style,
    )

    try:
        await app.run_async()
    except (EOFError, KeyboardInterrupt):
        pass

    console.print("\n[dim]PoC exited cleanly.[/dim]")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
