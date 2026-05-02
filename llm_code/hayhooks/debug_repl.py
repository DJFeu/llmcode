"""Debug REPL — migrated from ``llm_code/remote/server.py`` (M4.11).

The legacy ``RemoteServer`` exposed a WebSocket-driven REPL for remote
sessions. Hayhooks absorbs that surface as an **opt-in** sub-app at
``/debug/repl`` behind ``HayhooksConfig.enable_debug_repl`` (disabled
by default per security guidance). The wire format is preserved so
pre-v12 clients keep working after updating their URL.

This module is intentionally small; the old ``RemoteServer`` code
that streamed full ``ConversationRuntime`` events lives in
:class:`DebugReplServer`. The sub-app registered on the FastAPI stack
below is a minimal shim that shells out to :class:`DebugReplServer`.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Any

from llm_code.runtime.config import RuntimeConfig, load_config
from llm_code.runtime.model_aliases import resolve_model

logger = logging.getLogger(__name__)


def _bound_port(server: Any, fallback: int) -> int:
    sockets = getattr(server, "sockets", None) or []
    for sock in sockets:
        try:
            sockname = sock.getsockname()
        except OSError:
            continue
        if isinstance(sockname, tuple) and len(sockname) >= 2:
            port = sockname[1]
            if isinstance(port, int):
                return port
    return fallback


class DebugReplServer:
    """WebSocket REPL — exposes the llmcode conversation loop remotely.

    Wire format is identical to the pre-v12 ``llm_code.remote.server``
    implementation. Any code path that fell through to ``RemoteServer``
    should now point at ``DebugReplServer``; the behaviour only
    differs in where the object lives in the package tree.
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8765,
        config: RuntimeConfig | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._config = config
        self._runtime: Any | None = None

    async def start(self) -> None:
        """Start the WebSocket server; teardown runtime on exit.

        Kept intentionally wrapped in try/finally so that a SIGTERM or
        KeyboardInterrupt still drives ``self._runtime.shutdown()`` —
        Docker containers opened on behalf of this server exit cleanly
        instead of leaking.
        """
        try:
            import websockets
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "websockets is required for debug REPL; "
                "install llmcode-cli[websocket]"
            ) from exc

        async with websockets.serve(
            self._handle_client,
            self._host,
            self._port,
        ) as ws_server:
            self._port = _bound_port(ws_server, self._port)
            print(
                f"llm-code debug REPL listening on ws://{self._host}:{self._port}",
                flush=True,
            )
            try:
                await asyncio.Future()  # run forever
            finally:
                if self._runtime is not None:
                    try:
                        self._runtime.shutdown()
                    except Exception:
                        pass  # teardown must never raise

    # --- client handling ---------------------------------------------

    async def _handle_client(self, ws) -> None:
        print(f"Client connected: {ws.remote_address}")

        if not self._config:
            cwd = Path.cwd()
            self._config = load_config(
                user_dir=Path.home() / ".llmcode",
                project_dir=cwd / ".llmcode",
                local_path=cwd / ".llmcode" / "config.local.json",
                cli_overrides={},
            )

        self._init_session()

        cwd = Path.cwd()
        try:
            branch = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=cwd, capture_output=True, text=True, timeout=3,
            ).stdout.strip()
        except Exception:
            branch = ""

        await ws.send(json.dumps({
            "type": "welcome",
            "model": self._config.model,
            "workspace": cwd.name,
            "cwd": str(cwd),
            "permissions": self._config.permission_mode,
            "branch": branch,
        }))

        try:
            async for raw in ws:
                msg = json.loads(raw)
                await self._handle_message(ws, msg)
        except Exception:
            print(f"Client disconnected: {getattr(ws, 'remote_address', '?')}")

    async def _handle_message(self, ws, msg: dict) -> None:
        msg_type = msg.get("type", "")
        if msg_type == "user_input":
            text = msg.get("text", "").strip()
            if text.startswith("/"):
                await self._handle_command(ws, text)
            else:
                await self._run_turn(ws, text)

    async def _run_turn(self, ws, user_input: str) -> None:
        if not self._runtime:
            self._init_session()

        from llm_code.api.types import (
            StreamMessageStop,
            StreamTextDelta,
            StreamToolExecResult,
            StreamToolExecStart,
            StreamToolProgress,
        )

        await ws.send(json.dumps({"type": "user_echo", "text": user_input}))
        await ws.send(json.dumps({"type": "thinking_start"}))

        start = time.monotonic()
        text_buffer = ""
        output_tokens = 0

        in_tool_call = False
        in_think = False
        tag_buffer = ""

        try:
            async for event in self._runtime.run_turn(user_input):
                if isinstance(event, StreamTextDelta):
                    output_tokens += len(event.text) // 4

                    for char in event.text:
                        if in_tool_call:
                            tag_buffer += char
                            if tag_buffer.endswith("</tool_call>"):
                                in_tool_call = False
                                tag_buffer = ""
                        elif in_think:
                            tag_buffer += char
                            if tag_buffer.endswith("</think>"):
                                in_think = False
                                tag_buffer = ""
                        elif tag_buffer:
                            tag_buffer += char
                            if tag_buffer == "<tool_call>":
                                in_tool_call = True
                            elif tag_buffer == "<think>":
                                in_think = True
                            elif not "<tool_call>".startswith(tag_buffer) and not "<think>".startswith(tag_buffer):
                                text_buffer += tag_buffer
                                tag_buffer = ""
                        elif char == "<":
                            tag_buffer = "<"
                        else:
                            text_buffer += char

                    in_code = text_buffer.count("```") % 2 == 1
                    if not in_code and len(text_buffer) > 100:
                        await ws.send(json.dumps({"type": "text_delta", "text": text_buffer}))
                        text_buffer = ""

                elif isinstance(event, StreamToolExecStart):
                    if text_buffer:
                        await ws.send(json.dumps({"type": "text_delta", "text": text_buffer}))
                        text_buffer = ""
                    await ws.send(json.dumps({
                        "type": "tool_start",
                        "name": event.tool_name,
                        "detail": event.args_summary,
                    }))
                elif isinstance(event, StreamToolExecResult):
                    await ws.send(json.dumps({
                        "type": "tool_result",
                        "name": event.tool_name,
                        "output": event.output[:500],
                        "isError": event.is_error,
                    }))
                elif isinstance(event, StreamToolProgress):
                    await ws.send(json.dumps({
                        "type": "tool_progress",
                        "name": event.tool_name,
                        "message": event.message,
                    }))
                elif isinstance(event, StreamMessageStop):
                    if event.usage and event.usage.output_tokens > 0:
                        output_tokens = event.usage.output_tokens

        except Exception as exc:
            await ws.send(json.dumps({"type": "error", "message": str(exc)}))
            return

        if tag_buffer and not in_tool_call and not in_think:
            text_buffer += tag_buffer
        await ws.send(json.dumps({
            "type": "text_done",
            "text": text_buffer,
        }))

        elapsed = time.monotonic() - start
        await ws.send(json.dumps({
            "type": "thinking_stop",
            "elapsed": elapsed,
            "tokens": output_tokens,
        }))
        await ws.send(json.dumps({
            "type": "turn_done",
            "elapsed": elapsed,
            "tokens": output_tokens,
        }))

    async def _handle_command(self, ws, text: str) -> None:
        from llm_code.cli.commands import parse_slash_command

        cmd = parse_slash_command(text)
        if not cmd:
            return

        if cmd.name == "help":
            await ws.send(json.dumps({
                "type": "help",
                "commands": [
                    {"cmd": "/help", "desc": "Show commands"},
                    {"cmd": "/clear", "desc": "Clear conversation"},
                    {"cmd": "/cost", "desc": "Token usage"},
                    {"cmd": "/exit", "desc": "Disconnect"},
                ],
            }))
        elif cmd.name == "clear":
            self._init_session()
            await ws.send(json.dumps({
                "type": "message",
                "text": "Conversation cleared.",
            }))
        elif cmd.name == "cost":
            if self._runtime:
                u = self._runtime.session.total_usage
                await ws.send(json.dumps({
                    "type": "message",
                    "text": f"Tokens — in: {u.input_tokens:,}  out: {u.output_tokens:,}",
                }))
        elif cmd.name == "exit":
            await ws.close()
        else:
            await ws.send(json.dumps({
                "type": "message",
                "text": f"Command /{cmd.name} not available in remote mode.",
            }))

    def _init_session(self) -> None:
        """Same bootstrap sequence the pre-v12 RemoteServer used."""
        from llm_code.api.client import ProviderClient
        from llm_code.runtime.context import ProjectContext
        from llm_code.runtime.conversation import ConversationRuntime
        from llm_code.runtime.hooks import HookRunner
        from llm_code.runtime.permissions import PermissionMode, PermissionPolicy
        from llm_code.runtime.prompt import SystemPromptBuilder
        from llm_code.runtime.session import Session
        from llm_code.tools.bash import BashTool
        from llm_code.tools.edit_file import EditFileTool
        from llm_code.tools.glob_search import GlobSearchTool
        from llm_code.tools.grep_search import GrepSearchTool
        from llm_code.tools.read_file import ReadFileTool
        from llm_code.tools.registry import ToolRegistry
        from llm_code.tools.write_file import WriteFileTool

        model = resolve_model(self._config.model, self._config.model_aliases)
        api_key = os.environ.get(self._config.provider_api_key_env, "")

        provider = ProviderClient.from_model(
            model=model,
            base_url=self._config.provider_base_url or "",
            api_key=api_key,
            timeout=self._config.timeout,
            max_retries=self._config.max_retries,
            native_tools=self._config.native_tools,
        )

        registry = ToolRegistry()
        for cls in (ReadFileTool, WriteFileTool, EditFileTool, BashTool, GlobSearchTool, GrepSearchTool):
            registry.register(cls())

        try:
            from llm_code.tools.git_tools import (
                GitBranchTool,
                GitCommitTool,
                GitDiffTool,
                GitLogTool,
                GitPushTool,
                GitStashTool,
                GitStatusTool,
            )
            for cls in (
                GitStatusTool, GitDiffTool, GitLogTool, GitCommitTool,
                GitPushTool, GitStashTool, GitBranchTool,
            ):
                try:
                    registry.register(cls())
                except ValueError:
                    pass
        except ImportError:
            pass

        mode_map = {
            "read_only": PermissionMode.READ_ONLY,
            "workspace_write": PermissionMode.WORKSPACE_WRITE,
            "full_access": PermissionMode.FULL_ACCESS,
            "auto_accept": PermissionMode.AUTO_ACCEPT,
            "prompt": PermissionMode.PROMPT,
        }

        cwd = Path.cwd()
        self._runtime = ConversationRuntime(
            provider=provider,
            tool_registry=registry,
            permission_policy=PermissionPolicy(
                mode=mode_map.get(
                    self._config.permission_mode,
                    PermissionMode.PROMPT,
                ),
                allow_tools=self._config.allowed_tools,
                deny_tools=self._config.denied_tools,
            ),
            hook_runner=HookRunner(self._config.hooks),
            prompt_builder=SystemPromptBuilder(),
            config=self._config,
            session=Session.create(cwd),
            context=ProjectContext.discover(cwd),
        )


# --- Remote client + SSH proxy (ported) -------------------------------


async def ssh_connect(target: str, port: int = 8765) -> None:
    """SSH to ``target``, start llmcode debug REPL server, connect locally."""
    from rich.console import Console

    console = Console()
    console.print(f"[dim]Setting up SSH tunnel to {target}...[/]")

    ssh_cmd = [
        "ssh", "-tt",
        "-L", f"{port}:localhost:{port}",
        target,
        f"cd ~ && llmcode hayhooks serve --transport stdio --port {port}",
    ]
    console.print(f"[dim]$ {' '.join(ssh_cmd)}[/]")

    ssh_proc = subprocess.Popen(
        ssh_cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    await asyncio.sleep(3)

    if ssh_proc.poll() is not None:
        stderr = ssh_proc.stderr.read().decode() if ssh_proc.stderr else ""
        console.print(f"[red]SSH failed: {stderr[:200]}[/]")
        return

    console.print("[green]SSH tunnel established[/]")

    client = DebugReplClient(f"ws://localhost:{port}")
    try:
        await client.connect()
    finally:
        ssh_proc.terminate()
        ssh_proc.wait(timeout=5)
        console.print("[dim]SSH tunnel closed.[/]")


class DebugReplClient:
    """Thin terminal client for :class:`DebugReplServer`."""

    def __init__(self, url: str) -> None:
        self._url = url if url.startswith("ws") else f"ws://{url}"
        self._ws = None

    async def connect(self) -> None:
        try:
            import websockets
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "websockets is required for debug REPL client; "
                "install llmcode-cli[websocket]"
            ) from exc

        from rich.console import Console

        console = Console()
        console.print(f"[dim]Connecting to {self._url}...[/]")
        try:
            async with websockets.connect(self._url) as ws:
                self._ws = ws
                console.print("[green]Connected[/]")
                recv_task = asyncio.create_task(self._recv_loop(ws))
                while True:
                    try:
                        from prompt_toolkit import PromptSession
                        from prompt_toolkit.auto_suggest import AutoSuggestFromHistory

                        session = PromptSession(auto_suggest=AutoSuggestFromHistory())
                        user_input = await session.prompt_async("> ")
                    except (EOFError, KeyboardInterrupt):
                        console.print("\n[dim]Disconnecting...[/]")
                        break
                    user_input = user_input.strip()
                    if not user_input:
                        continue
                    if user_input in ("/exit", "/quit"):
                        break
                    await ws.send(json.dumps({
                        "type": "user_input",
                        "text": user_input,
                    }))
                recv_task.cancel()
        except ConnectionRefusedError:
            console.print(f"[red]Cannot connect to {self._url}[/]")
        except Exception as exc:  # noqa: BLE001
            console.print(f"[red]Connection error: {exc}[/]")

    async def _recv_loop(self, ws) -> None:  # pragma: no cover — interactive
        try:
            async for raw in ws:
                msg = json.loads(raw)
                self._render_event(msg)
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    def _render_event(self, msg: dict) -> None:  # pragma: no cover — UI
        from rich.console import Console

        console = Console()
        mt = msg.get("type", "")
        if mt == "welcome":
            console.print(f"[bold cyan]llm-code[/] [dim]remote[/] model={msg.get('model')}")
        elif mt == "text_delta":
            console.print(msg.get("text", ""))
        elif mt == "text_done":
            console.print(msg.get("text", ""))
        elif mt == "error":
            console.print(f"[red]Error: {msg.get('message')}[/]")


# --- FastAPI mount ----------------------------------------------------


def register_debug_repl_routes(app: Any, config: Any) -> None:
    """Expose a minimal health endpoint under ``/debug/repl``.

    Full REPL streaming is still served by :class:`DebugReplServer`
    over raw websockets; the HTTP sub-app simply confirms the feature
    is enabled so callers can probe the deployment.
    """
    try:
        from fastapi import APIRouter
    except ImportError:
        return

    router = APIRouter(prefix="/debug")

    @router.get("/repl/health")
    async def repl_health() -> dict:
        return {
            "status": "ok",
            "enabled": bool(getattr(config, "enable_debug_repl", False)),
        }

    app.include_router(router)
