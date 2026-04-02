"""Python ↔ Ink IPC bridge for llm-code."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

from llm_code.runtime.config import RuntimeConfig


class InkBridge:
    """Manages communication between Python backend and Ink frontend."""

    def __init__(self, config: RuntimeConfig, cwd: Path, budget: int | None = None):
        self._config = config
        self._cwd = cwd
        self._budget = budget
        self._ink_process: asyncio.subprocess.Process | None = None
        self._runtime = None
        self._skills = None
        self._memory = None
        self._project_index = None
        self._checkpoint_mgr = None

    async def start(self) -> None:
        """Start the Ink frontend process and begin communication."""
        # Find ink-ui location
        ink_dir = self._find_ink_dir()

        # Spawn Ink process
        self._ink_process = await asyncio.create_subprocess_exec(
            "npx", "tsx", str(ink_dir / "src" / "index.tsx"),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=None,  # Pass through to terminal — Ink renders here
            cwd=str(ink_dir),
        )

        # Initialize backend
        self._init_session()

        # Send welcome
        branch = self._detect_git_branch()
        await self._send({
            "type": "welcome",
            "model": self._config.model or "(not set)",
            "workspace": self._cwd.name,
            "cwd": str(self._cwd),
            "permissions": self._config.permission_mode or "prompt",
            "branch": branch,
        })

        # Start reading from Ink frontend
        await self._read_loop()

    async def _send(self, msg: dict) -> None:
        """Send a JSON message to Ink frontend."""
        if self._ink_process and self._ink_process.stdin:
            line = json.dumps(msg, ensure_ascii=False) + "\n"
            self._ink_process.stdin.write(line.encode())
            await self._ink_process.stdin.drain()

    async def _read_loop(self) -> None:
        """Read messages from Ink frontend and handle them."""
        if not self._ink_process or not self._ink_process.stdout:
            return

        while True:
            line = await self._ink_process.stdout.readline()
            if not line:
                break  # Process exited

            try:
                msg = json.loads(line.decode().strip())
            except json.JSONDecodeError:
                continue

            await self._handle_frontend_message(msg)

    async def _handle_frontend_message(self, msg: dict) -> None:
        """Handle a message from the Ink frontend."""
        msg_type = msg.get("type", "")

        if msg_type == "user_input":
            text = msg.get("text", "").strip()
            if not text:
                return

            if text.startswith("/"):
                await self._handle_slash_command(text)
            else:
                await self._run_turn(text)

        elif msg_type == "permission_response":
            # TODO: wire into permission system
            pass

        elif msg_type == "image_paste":
            # TODO: handle image paste
            pass

    async def _run_turn(self, user_input: str, images=None) -> None:
        """Run a conversation turn and stream events to Ink."""
        if not self._runtime:
            self._init_session()

        await self._send({"type": "user_echo", "text": user_input})
        await self._send({"type": "thinking_start"})

        from llm_code.api.types import (
            StreamTextDelta, StreamToolExecStart, StreamToolExecResult,
            StreamToolProgress, StreamMessageStop,
        )

        start = time.monotonic()
        text_buffer = ""
        output_tokens = 0
        in_tool_call = False
        in_think = False
        tag_buffer = ""

        try:
            async for event in self._runtime.run_turn(user_input, images=images):
                if isinstance(event, StreamTextDelta):
                    output_tokens += len(event.text) // 4

                    # Filter <tool_call> and <think> tags
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

                    # Send text deltas periodically
                    if text_buffer.endswith("\n\n") or len(text_buffer) > 200:
                        in_code = text_buffer.count("```") % 2 == 1
                        if not in_code:
                            await self._send({"type": "text_delta", "text": text_buffer})
                            text_buffer = ""

                elif isinstance(event, StreamToolExecStart):
                    # Flush any pending text
                    if text_buffer:
                        await self._send({"type": "text_delta", "text": text_buffer})
                        text_buffer = ""
                    await self._send({"type": "thinking_stop", "elapsed": time.monotonic() - start, "tokens": 0})
                    await self._send({"type": "tool_start", "name": event.tool_name, "detail": event.args_summary})

                elif isinstance(event, StreamToolExecResult):
                    await self._send({
                        "type": "tool_result",
                        "name": event.tool_name,
                        "output": event.output[:500],
                        "isError": event.is_error,
                    })

                elif isinstance(event, StreamToolProgress):
                    await self._send({"type": "tool_progress", "name": event.tool_name, "message": event.message})

                elif isinstance(event, StreamMessageStop):
                    if event.usage and event.usage.output_tokens > 0:
                        output_tokens = event.usage.output_tokens

        except Exception as exc:
            await self._send({"type": "error", "message": str(exc)})
            return

        # Flush remaining text
        if tag_buffer and not in_tool_call and not in_think:
            text_buffer += tag_buffer
        if text_buffer:
            await self._send({"type": "text_done", "text": text_buffer})
        else:
            await self._send({"type": "text_done", "text": ""})

        elapsed = time.monotonic() - start
        await self._send({"type": "thinking_stop", "elapsed": elapsed, "tokens": output_tokens})
        await self._send({"type": "turn_done", "elapsed": elapsed, "tokens": output_tokens})

    async def _handle_slash_command(self, text: str) -> None:
        """Handle slash commands by delegating to the print-based CLI handler."""
        from llm_code.cli.commands import parse_slash_command
        cmd = parse_slash_command(text)
        if not cmd:
            return

        name = cmd.name
        args = cmd.args.strip()

        if name in ("exit", "quit"):
            if self._ink_process:
                self._ink_process.terminate()
            return

        elif name == "help":
            commands = [
                {"cmd": "/help", "desc": "Show this help"},
                {"cmd": "/clear", "desc": "Clear conversation"},
                {"cmd": "/model <name>", "desc": "Switch model"},
                {"cmd": "/skill", "desc": "Browse skills"},
                {"cmd": "/mcp", "desc": "Browse MCP servers"},
                {"cmd": "/plugin", "desc": "Browse plugins"},
                {"cmd": "/memory", "desc": "Project memory"},
                {"cmd": "/undo", "desc": "Undo last change"},
                {"cmd": "/cost", "desc": "Token usage"},
                {"cmd": "/exit", "desc": "Quit"},
            ]
            await self._send({"type": "help", "commands": commands})

        elif name == "clear":
            self._init_session()
            await self._send({"type": "message", "text": "Conversation cleared."})

        elif name == "cost":
            if self._runtime:
                u = self._runtime.session.total_usage
                await self._send({"type": "message", "text": f"Tokens — in: {u.input_tokens:,}  out: {u.output_tokens:,}"})

        else:
            await self._send({"type": "message", "text": f"Command /{name} — use the print-based CLI for full marketplace support."})

    def _init_session(self) -> None:
        """Initialize the conversation runtime — same as LLMCodeCLI._init_session."""
        import dataclasses
        from llm_code.api.client import ProviderClient
        from llm_code.runtime.context import ProjectContext
        from llm_code.runtime.conversation import ConversationRuntime
        from llm_code.runtime.hooks import HookRunner
        from llm_code.runtime.permissions import PermissionMode, PermissionPolicy
        from llm_code.runtime.prompt import SystemPromptBuilder
        from llm_code.runtime.session import Session, SessionManager
        from llm_code.tools.registry import ToolRegistry

        # Build provider
        api_key = os.environ.get(self._config.provider_api_key_env, "")
        provider = ProviderClient.from_model(
            model=self._config.model,
            base_url=self._config.provider_base_url or "",
            api_key=api_key,
            timeout=self._config.timeout,
            max_retries=self._config.max_retries,
            native_tools=self._config.native_tools,
        )

        # Build tool registry
        from llm_code.tools.read_file import ReadFileTool
        from llm_code.tools.write_file import WriteFileTool
        from llm_code.tools.edit_file import EditFileTool
        from llm_code.tools.bash import BashTool
        from llm_code.tools.glob_search import GlobSearchTool
        from llm_code.tools.grep_search import GrepSearchTool

        registry = ToolRegistry()
        for cls in (ReadFileTool, WriteFileTool, EditFileTool, BashTool, GlobSearchTool, GrepSearchTool):
            registry.register(cls())

        # Git tools
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

        # Permissions
        mode_map = {
            "read_only": PermissionMode.READ_ONLY,
            "workspace_write": PermissionMode.WORKSPACE_WRITE,
            "full_access": PermissionMode.FULL_ACCESS,
            "auto_accept": PermissionMode.AUTO_ACCEPT,
            "prompt": PermissionMode.PROMPT,
        }
        permissions = PermissionPolicy(
            mode=mode_map.get(self._config.permission_mode, PermissionMode.PROMPT),
            allow_tools=self._config.allowed_tools,
            deny_tools=self._config.denied_tools,
        )

        context = ProjectContext.discover(self._cwd)
        session = Session.create(self._cwd)
        hooks = HookRunner(self._config.hooks)

        # Checkpoint
        checkpoint_mgr = None
        if (self._cwd / ".git").is_dir():
            try:
                from llm_code.runtime.checkpoint import CheckpointManager
                checkpoint_mgr = CheckpointManager(self._cwd)
            except Exception:
                pass

        # Skills
        try:
            from llm_code.runtime.skills import SkillLoader
            from llm_code.marketplace.installer import PluginInstaller
            skill_dirs = [
                Path.home() / ".llm-code" / "skills",
                self._cwd / ".llm-code" / "skills",
            ]
            plugin_dir = Path.home() / ".llm-code" / "plugins"
            if plugin_dir.is_dir():
                pi = PluginInstaller(plugin_dir)
                for p in pi.list_installed():
                    if p.enabled:
                        direct = p.path / "skills"
                        if direct.is_dir():
                            skill_dirs.append(direct)
            self._skills = SkillLoader().load_from_dirs(skill_dirs)
        except Exception:
            self._skills = None

        prompt_builder = SystemPromptBuilder()

        self._runtime = ConversationRuntime(
            provider=provider,
            tool_registry=registry,
            permission_policy=permissions,
            hook_runner=hooks,
            prompt_builder=prompt_builder,
            config=self._config,
            session=session,
            context=context,
            checkpoint_manager=checkpoint_mgr,
        )

    def _detect_git_branch(self) -> str:
        import subprocess
        try:
            r = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=self._cwd,
                capture_output=True,
                text=True,
                timeout=3,
            )
            return r.stdout.strip() if r.returncode == 0 else ""
        except Exception:
            return ""

    def _find_ink_dir(self) -> Path:
        """Find the ink-ui directory."""
        # Check relative to this file
        pkg_dir = Path(__file__).resolve().parent.parent.parent
        ink_dir = pkg_dir / "ink-ui"
        if ink_dir.is_dir() and (ink_dir / "package.json").exists():
            return ink_dir
        # Check relative to cwd
        ink_dir = self._cwd / "ink-ui"
        if ink_dir.is_dir():
            return ink_dir
        raise FileNotFoundError("ink-ui/ directory not found. Run 'cd ink-ui && npm install' first.")
