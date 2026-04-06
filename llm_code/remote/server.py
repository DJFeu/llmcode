"""Remote server — runs on the remote machine, executes tools locally."""
from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

import websockets
from websockets.asyncio.server import ServerConnection

from llm_code.runtime.config import RuntimeConfig, load_config
from llm_code.runtime.model_aliases import resolve_model


class RemoteServer:
    def __init__(self, host: str = "0.0.0.0", port: int = 8765, config: RuntimeConfig | None = None):
        self._host = host
        self._port = port
        self._config = config
        self._runtime = None
        self._skills = None
        self._memory = None

    async def start(self) -> None:
        """Start WebSocket server."""
        print(f"llm-code server listening on ws://{self._host}:{self._port}")
        async with websockets.serve(self._handle_client, self._host, self._port):
            await asyncio.Future()  # run forever

    async def _handle_client(self, ws: ServerConnection) -> None:
        """Handle a connected client."""
        print(f"Client connected: {ws.remote_address}")

        # Load config if not provided
        if not self._config:
            cwd = Path.cwd()
            self._config = load_config(
                user_dir=Path.home() / ".llmcode",
                project_dir=cwd,
                local_path=cwd / ".llmcode" / "config.json",
                cli_overrides={},
            )

        # Initialize session
        self._init_session()

        # Send welcome
        cwd = Path.cwd()
        import subprocess
        try:
            branch = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=cwd, capture_output=True, text=True, timeout=3
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

        # Main message loop
        try:
            async for raw in ws:
                msg = json.loads(raw)
                await self._handle_message(ws, msg)
        except websockets.ConnectionClosed:
            print(f"Client disconnected: {ws.remote_address}")

    async def _handle_message(self, ws: ServerConnection, msg: dict) -> None:
        msg_type = msg.get("type", "")

        if msg_type == "user_input":
            text = msg.get("text", "").strip()
            if text.startswith("/"):
                await self._handle_command(ws, text)
            else:
                await self._run_turn(ws, text)

    async def _run_turn(self, ws: ServerConnection, user_input: str) -> None:
        """Run a conversation turn, streaming events to client."""
        if not self._runtime:
            self._init_session()

        from llm_code.api.types import (
            StreamTextDelta, StreamToolExecStart, StreamToolExecResult,
            StreamToolProgress, StreamMessageStop,
        )

        await ws.send(json.dumps({"type": "user_echo", "text": user_input}))
        await ws.send(json.dumps({"type": "thinking_start"}))

        start = time.monotonic()
        text_buffer = ""
        output_tokens = 0

        # Tag filtering state
        in_tool_call = False
        in_think = False
        tag_buffer = ""

        try:
            async for event in self._runtime.run_turn(user_input):
                if isinstance(event, StreamTextDelta):
                    output_tokens += len(event.text) // 4

                    # Filter tags
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

                    # Flush periodically
                    in_code = text_buffer.count("```") % 2 == 1
                    if not in_code and len(text_buffer) > 100:
                        await ws.send(json.dumps({"type": "text_delta", "text": text_buffer}))
                        text_buffer = ""

                elif isinstance(event, StreamToolExecStart):
                    if text_buffer:
                        await ws.send(json.dumps({"type": "text_delta", "text": text_buffer}))
                        text_buffer = ""
                    await ws.send(json.dumps({"type": "tool_start", "name": event.tool_name, "detail": event.args_summary}))

                elif isinstance(event, StreamToolExecResult):
                    await ws.send(json.dumps({"type": "tool_result", "name": event.tool_name, "output": event.output[:500], "isError": event.is_error}))

                elif isinstance(event, StreamToolProgress):
                    await ws.send(json.dumps({"type": "tool_progress", "name": event.tool_name, "message": event.message}))

                elif isinstance(event, StreamMessageStop):
                    if event.usage and event.usage.output_tokens > 0:
                        output_tokens = event.usage.output_tokens

        except Exception as exc:
            await ws.send(json.dumps({"type": "error", "message": str(exc)}))
            return

        # Flush remaining
        if tag_buffer and not in_tool_call and not in_think:
            text_buffer += tag_buffer
        if text_buffer:
            await ws.send(json.dumps({"type": "text_done", "text": text_buffer}))
        else:
            await ws.send(json.dumps({"type": "text_done", "text": ""}))

        elapsed = time.monotonic() - start
        await ws.send(json.dumps({"type": "thinking_stop", "elapsed": elapsed, "tokens": output_tokens}))
        await ws.send(json.dumps({"type": "turn_done", "elapsed": elapsed, "tokens": output_tokens}))

    async def _handle_command(self, ws: ServerConnection, text: str) -> None:
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
            await ws.send(json.dumps({"type": "message", "text": "Conversation cleared."}))
        elif cmd.name == "cost":
            if self._runtime:
                u = self._runtime.session.total_usage
                await ws.send(json.dumps({"type": "message", "text": f"Tokens — in: {u.input_tokens:,}  out: {u.output_tokens:,}"}))
        elif cmd.name == "exit":
            await ws.close()
        else:
            await ws.send(json.dumps({"type": "message", "text": f"Command /{cmd.name} not available in remote mode."}))

    def _init_session(self) -> None:
        """Initialize ConversationRuntime — same pattern as tui.py."""
        from llm_code.api.client import ProviderClient
        from llm_code.runtime.context import ProjectContext
        from llm_code.runtime.conversation import ConversationRuntime
        from llm_code.runtime.hooks import HookRunner
        from llm_code.runtime.permissions import PermissionMode, PermissionPolicy
        from llm_code.runtime.prompt import SystemPromptBuilder
        from llm_code.runtime.session import Session
        from llm_code.tools.registry import ToolRegistry

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

        from llm_code.tools.read_file import ReadFileTool
        from llm_code.tools.write_file import WriteFileTool
        from llm_code.tools.edit_file import EditFileTool
        from llm_code.tools.bash import BashTool
        from llm_code.tools.glob_search import GlobSearchTool
        from llm_code.tools.grep_search import GrepSearchTool

        registry = ToolRegistry()
        for cls in (ReadFileTool, WriteFileTool, EditFileTool, BashTool, GlobSearchTool, GrepSearchTool):
            registry.register(cls())

        try:
            from llm_code.tools.git_tools import (
                GitStatusTool, GitDiffTool, GitLogTool, GitCommitTool,
                GitPushTool, GitStashTool, GitBranchTool,
            )
            for cls in (GitStatusTool, GitDiffTool, GitLogTool, GitCommitTool, GitPushTool, GitStashTool, GitBranchTool):
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
                mode=mode_map.get(self._config.permission_mode, PermissionMode.PROMPT),
                allow_tools=self._config.allowed_tools,
                deny_tools=self._config.denied_tools,
            ),
            hook_runner=HookRunner(self._config.hooks),
            prompt_builder=SystemPromptBuilder(),
            config=self._config,
            session=Session.create(cwd),
            context=ProjectContext.discover(cwd),
        )
