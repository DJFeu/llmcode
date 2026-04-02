"""SSH proxy — SSH to remote host, auto-start server, connect."""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys


async def ssh_connect(target: str, port: int = 8765) -> None:
    """SSH to target, start llm-code server, connect locally.

    target: user@host or just host
    """
    from rich.console import Console
    console = Console()

    console.print(f"[dim]Setting up SSH tunnel to {target}...[/]")

    # Start SSH tunnel: forward local port to remote
    # Also start llm-code --serve on remote
    ssh_cmd = [
        "ssh", "-tt",
        "-L", f"{port}:localhost:{port}",
        target,
        f"cd ~ && llm-code --serve --port {port}",
    ]

    console.print(f"[dim]$ {' '.join(ssh_cmd)}[/]")

    # Start SSH in background
    ssh_proc = subprocess.Popen(
        ssh_cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    # Wait a moment for server to start
    await asyncio.sleep(3)

    if ssh_proc.poll() is not None:
        stderr = ssh_proc.stderr.read().decode() if ssh_proc.stderr else ""
        console.print(f"[red]SSH failed: {stderr[:200]}[/]")
        return

    console.print(f"[green]✓ SSH tunnel established[/]")

    # Connect to local forwarded port
    from llm_code.remote.client import RemoteClient
    client = RemoteClient(f"ws://localhost:{port}")

    try:
        await client.connect()
    finally:
        ssh_proc.terminate()
        ssh_proc.wait(timeout=5)
        console.print("[dim]SSH tunnel closed.[/]")
