"""Python ↔ Ink IPC bridge for llm-code."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

from llm_code.logging import get_logger
from llm_code.runtime.config import RuntimeConfig
from llm_code.runtime.cost_tracker import CostTracker
from llm_code.runtime.model_aliases import resolve_model

logger = get_logger(__name__)


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
        self._current_marketplace: dict | None = None
        self._selected_item: dict | None = None
        self._skill_items: list[dict] = []
        self._cost_tracker = CostTracker(
            model=self._config.model,
            custom_pricing=self._config.pricing or None,
            max_budget_usd=self._config.max_budget_usd,
        )

    async def start(self) -> None:
        """Start the Ink frontend process and begin communication."""
        # Find ink-ui location
        ink_dir = self._find_ink_dir()

        # Spawn Ink process with forced color support
        import os as _os
        env = {**_os.environ, "FORCE_COLOR": "3", "COLORTERM": "truecolor"}
        self._ink_process = await asyncio.create_subprocess_exec(
            "npx", "tsx", str(ink_dir / "src" / "index.tsx"),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=None,  # Pass through to terminal — Ink renders here
            cwd=str(ink_dir),
            env=env,
        )

        # Initialize backend
        self._init_session()
        await self._init_mcp_servers()

        # Start cron scheduler
        self._cron_scheduler_task = None
        if getattr(self, "_cron_storage", None) is not None:
            try:
                from llm_code.cron.scheduler import CronScheduler

                async def _on_cron_fire(prompt: str) -> None:
                    await self._run_turn(prompt)

                lock_path = self._cwd / ".llm-code" / "cron.lock"
                self._cron_scheduler = CronScheduler(self._cron_storage, lock_path, _on_cron_fire)
                self._cron_scheduler_task = asyncio.create_task(self._cron_scheduler.start())
            except Exception:
                pass

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

        # Non-blocking version check — fire and forget
        async def _version_check_bg() -> None:
            try:
                from llm_code.utils.version_check import check_latest_version
                info = await check_latest_version("0.1.0")
                if info and info.is_outdated:
                    await self._send({
                        "type": "system_message",
                        "text": (
                            f"Update available: v{info.current} → v{info.latest}. "
                            "pip install --upgrade llm-code"
                        ),
                    })
            except Exception:
                pass

        asyncio.ensure_future(_version_check_bg())

        # Start reading from Ink frontend
        await self._read_loop()

        # Stop cron scheduler on exit
        if getattr(self, "_cron_scheduler_task", None) is not None:
            try:
                self._cron_scheduler.stop()
                self._cron_scheduler_task.cancel()
            except Exception:
                pass

        # On exit: auto-save session + generate summary
        await self._auto_save_on_exit()

    async def _send(self, msg: dict) -> None:
        """Send a JSON message to Ink frontend."""
        if self._ink_process and self._ink_process.stdin:
            try:
                line = json.dumps(msg, ensure_ascii=False) + "\n"
                self._ink_process.stdin.write(line.encode())
                await asyncio.wait_for(self._ink_process.stdin.drain(), timeout=3.0)
            except Exception as e:
                import logging
                logging.getLogger(__name__).debug("IPC send error: %s", e)

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
            elif text.isdigit() and hasattr(self, '_skill_items') and self._skill_items:
                # Number selection from skill/plugin list
                idx = int(text) - 1
                if 0 <= idx < len(self._skill_items):
                    item = self._skill_items[idx]
                    name = item["name"]
                    installed = item.get("installed", False)
                    if installed:
                        await self._send({"type": "message", "text": f"Selected: {name}\n  /skill disable {name}\n  /skill remove {name}"})
                    else:
                        await self._send({"type": "message", "text": f"Installing {name}..."})
                        # Attempt install
                        if name.startswith("clawhub:"):
                            await self._send({"type": "message", "text": f"ClawHub skill: npx clawhub@latest install {name.replace('clawhub:', '')}"})
                        else:
                            await self._handle_slash_command(f"/skill install {name}")
                    self._skill_items = []  # Clear after selection
                else:
                    await self._send({"type": "message", "text": f"Invalid number: {text}"})
            else:
                self._skill_items = []  # Clear on any non-number input
                await self._run_turn(text)

        elif msg_type == "permission_response":
            # TODO: wire into permission system
            pass

        elif msg_type == "marketplace_select":
            index = msg.get("index", -1)
            await self._handle_marketplace_selection(index)

        elif msg_type == "action_select":
            action_id = msg.get("actionId", "")
            await self._handle_marketplace_action(action_id)

        elif msg_type == "marketplace_close":
            self._current_marketplace = None
            self._selected_item = None

        elif msg_type == "image_paste":
            # TODO: handle image paste
            pass

        elif msg_type == "voice_toggle":
            await self._handle_voice_toggle()

    async def _handle_voice_toggle(self) -> None:
        """Handle voice recording toggle from Ink frontend."""
        if not hasattr(self, "_voice_recorder") or self._voice_recorder is None:
            from llm_code.voice.recorder import AudioRecorder, detect_backend
            from llm_code.voice.stt import create_stt_engine
            try:
                backend = detect_backend()
                self._voice_recorder = AudioRecorder(backend=backend)
                self._stt_engine = create_stt_engine(self._config.voice)
            except RuntimeError as exc:
                await self._send({"type": "error", "message": str(exc)})
                return

        if not getattr(self, "_voice_recording", False):
            self._voice_recording = True
            self._voice_recorder.start()
            await self._send({"type": "voice_start"})
        else:
            self._voice_recording = False
            audio_bytes = self._voice_recorder.stop()
            await self._send({"type": "voice_stop"})

            if audio_bytes:
                try:
                    text = self._stt_engine.transcribe(
                        audio_bytes, self._config.voice.language
                    )
                    if text.strip():
                        await self._send({"type": "voice_text", "text": text.strip()})
                except Exception as exc:
                    await self._send({"type": "error", "message": f"STT error: {exc}"})

    async def _run_turn(self, user_input: str, images=None) -> None:
        """Run a conversation turn and stream events to Ink."""
        if not self._runtime:
            self._init_session()

        await self._send({"type": "user_echo", "text": user_input})
        await self._send({"type": "thinking_start"})

        from llm_code.api.types import (
            StreamTextDelta, StreamThinkingDelta, StreamToolExecStart, StreamToolExecResult,
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

                elif isinstance(event, StreamThinkingDelta):
                    await self._send({"type": "thinking_delta", "text": event.text})

                elif isinstance(event, StreamToolExecStart):
                    # Flush any pending text
                    if text_buffer:
                        await self._send({"type": "text_delta", "text": text_buffer})
                        text_buffer = ""
                    await self._send({"type": "thinking_stop", "elapsed": time.monotonic() - start, "tokens": 0})
                    await self._send({"type": "tool_start", "name": event.tool_name, "detail": event.args_summary})

                elif isinstance(event, StreamToolExecResult):
                    msg = {
                        "type": "tool_result",
                        "name": event.tool_name,
                        "output": event.output[:500],
                        "isError": event.is_error,
                    }
                    if event.metadata and "diff" in event.metadata:
                        msg["diff"] = {
                            "hunks": event.metadata["diff"],
                            "additions": event.metadata.get("additions", 0),
                            "deletions": event.metadata.get("deletions", 0),
                        }
                    await self._send(msg)

                elif isinstance(event, StreamToolProgress):
                    await self._send({"type": "tool_progress", "name": event.tool_name, "message": event.message})

                elif isinstance(event, StreamMessageStop):
                    if event.usage and event.usage.output_tokens > 0:
                        output_tokens = event.usage.output_tokens
                        self._cost_tracker.add_usage(
                            event.usage.input_tokens, event.usage.output_tokens
                        )

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
                {"cmd": "/config", "desc": "View/set runtime config"},
                {"cmd": "/memory", "desc": "Project memory"},
                {"cmd": "/undo", "desc": "Undo last change"},
                {"cmd": "/cost", "desc": "Token usage"},
                {"cmd": "/search <query>", "desc": "Search conversation history"},
                {"cmd": "/thinking", "desc": "Toggle thinking mode"},
                {"cmd": "/vim", "desc": "Toggle vim mode"},
                {"cmd": "/voice", "desc": "Toggle voice input"},
                {"cmd": "/task", "desc": "Task lifecycle"},
                {"cmd": "/swarm", "desc": "Multi-agent swarm"},
                {"cmd": "/cron", "desc": "Scheduled tasks"},
                {"cmd": "/vcr", "desc": "Session recording"},
                {"cmd": "/checkpoint", "desc": "Session checkpoints"},
                {"cmd": "/ide", "desc": "IDE connection"},
                {"cmd": "/hida", "desc": "Task classification"},
                {"cmd": "/lsp", "desc": "LSP status"},
                {"cmd": "/exit", "desc": "Quit"},
            ]
            await self._send({"type": "help", "commands": commands})

        elif name == "clear":
            self._init_session()
            await self._send({"type": "message", "text": "Conversation cleared."})

        elif name == "model":
            if args:
                import dataclasses
                self._config = dataclasses.replace(self._config, model=args)
                self._init_session()
                await self._send({"type": "message", "text": f"Model switched to: {args}"})
            else:
                await self._send({"type": "message", "text": f"Current model: {self._config.model or '(not set)'}"})

        elif name == "cost":
            await self._send({"type": "message", "text": self._cost_tracker.format_cost()})

        elif name == "cd":
            if args:
                new_path = Path(args).expanduser()
                if not new_path.is_absolute():
                    new_path = self._cwd / new_path
                if new_path.is_dir():
                    self._cwd = new_path
                    os.chdir(new_path)
                    await self._send({"type": "message", "text": f"Directory: {new_path}"})
                else:
                    await self._send({"type": "error", "message": f"Not found: {new_path}"})
            else:
                await self._send({"type": "message", "text": f"Directory: {self._cwd}"})

        elif name == "budget":
            if args:
                try:
                    self._budget = int(args)
                    await self._send({"type": "message", "text": f"Budget: {self._budget:,} tokens"})
                except ValueError:
                    await self._send({"type": "error", "message": "Usage: /budget <number>"})
            else:
                await self._send({"type": "message", "text": f"Budget: {self._budget or 'none'}"})

        elif name == "memory":
            if self._memory:
                mem_parts = args.strip().split(None, 1)
                mem_subcmd = mem_parts[0] if mem_parts else ""

                if mem_subcmd == "consolidate":
                    if not self._runtime:
                        await self._send({"type": "error", "message": "No active session to consolidate."})
                    else:
                        await self._send({"type": "message", "text": "Consolidating session..."})
                        try:
                            from llm_code.runtime.dream import DreamTask
                            dream = DreamTask()
                            result = await dream.consolidate(
                                self._runtime.session,
                                self._memory,
                                self._runtime._provider,
                                self._config,
                            )
                            if result:
                                await self._send({"type": "message", "text": f"Consolidated:\n{result[:500]}"})
                            else:
                                await self._send({"type": "message", "text": "Nothing to consolidate (too few turns or disabled)."})
                        except Exception as e:
                            await self._send({"type": "error", "message": f"Consolidation failed: {e}"})

                elif mem_subcmd == "history":
                    summaries = self._memory.load_consolidated_summaries(limit=5)
                    if not summaries:
                        await self._send({"type": "message", "text": "No consolidated memories yet."})
                    else:
                        lines = [f"Consolidated Memories ({len(summaries)} most recent)"]
                        for i, s in enumerate(summaries):
                            preview = "\n".join(s.strip().splitlines()[:3])
                            lines.append(f"#{i+1} {preview}")
                        await self._send({"type": "message", "text": "\n\n".join(lines)})

                else:
                    entries = self._memory.get_all()
                    if entries:
                        lines = [f"Memory ({len(entries)} entries)"]
                        for k, v in entries.items():
                            lines.append(f"  {k}: {v.value[:60]}")
                        await self._send({"type": "message", "text": "\n".join(lines)})
                    else:
                        await self._send({"type": "message", "text": "No memories stored."})
            else:
                await self._send({"type": "message", "text": "Memory not initialized."})

        elif name == "undo":
            if hasattr(self, '_checkpoint_mgr') and self._checkpoint_mgr:
                if self._checkpoint_mgr.can_undo():
                    cp = self._checkpoint_mgr.undo()
                    if cp:
                        await self._send({"type": "message", "text": f"✓ Undone: {cp.tool_name}"})
                else:
                    await self._send({"type": "message", "text": "Nothing to undo."})
            else:
                await self._send({"type": "error", "message": "Not in a git repo."})

        elif name == "index":
            if args == "rebuild":
                from llm_code.runtime.indexer import ProjectIndexer
                idx = ProjectIndexer(self._cwd).build_index()
                await self._send({"type": "message", "text": f"Index rebuilt: {len(idx.files)} files, {len(idx.symbols)} symbols"})
            else:
                await self._send({"type": "message", "text": "Use /index rebuild"})

        elif name == "image":
            await self._send({"type": "message", "text": "Use Cmd+V to paste image, or type the path after your message."})

        elif name == "config":
            await self._handle_config_command(args)

        elif name == "session":
            await self._send({"type": "message", "text": "Sessions: /session list · /session save (use print-based CLI for full support)"})

        elif name == "skill":
            await self._show_skill_marketplace()

        elif name == "mcp":
            await self._show_mcp_marketplace()

        elif name == "plugin":
            await self._show_plugin_marketplace()

        elif name == "thinking":
            mode_map = {"on": "enabled", "off": "disabled", "adaptive": "adaptive"}
            if args in mode_map:
                new_mode = mode_map[args]
                from llm_code.runtime.config import ThinkingConfig
                import dataclasses as _dc
                new_thinking = ThinkingConfig(mode=new_mode, budget_tokens=self._config.thinking.budget_tokens)
                self._config = _dc.replace(self._config, thinking=new_thinking)
                if self._runtime:
                    self._runtime._config = self._config
                await self._send({"type": "message", "text": f"Thinking mode: {new_mode}", "style": "info"})
            else:
                current = self._config.thinking.mode
                budget = self._config.thinking.budget_tokens
                await self._send({
                    "type": "message",
                    "text": f"Thinking: {current} (budget: {budget} tokens)\nUsage: /thinking [adaptive|on|off]",
                    "style": "info",
                })

        elif name == "vim":
            import dataclasses as _dc
            current = getattr(self._config, "vim_mode", False)
            new_val = not current
            self._config = _dc.replace(self._config, vim_mode=new_val)
            status = "enabled" if new_val else "disabled"
            await self._send({"type": "message", "text": f"Vim mode {status}"})

        elif name == "voice":
            voice_cfg = getattr(self._config, "voice", None)
            if voice_cfg:
                import dataclasses as _dc
                new_voice = _dc.replace(voice_cfg, enabled=not voice_cfg.enabled)
                self._config = _dc.replace(self._config, voice=new_voice)
                status = "enabled" if new_voice.enabled else "disabled"
                await self._send({"type": "message", "text": f"Voice input {status}"})
            else:
                await self._send({"type": "message", "text": "Voice not configured. Add voice section to config."})

        elif name == "checkpoint":
            await self._handle_checkpoint_command(args)

        elif name == "lsp":
            await self._send({"type": "message", "text": "LSP: use /lsp status to check language server connections"})

        elif name == "cancel":
            await self._send({"type": "thinking_stop", "elapsed": 0, "tokens": 0})
            await self._send({"type": "message", "text": "(cancelled)"})

        elif name == "cron":
            await self._handle_cron_command(args)

        elif name == "ide":
            await self._handle_ide_command(args)

        elif name == "search":
            await self._handle_search_command(args)

        elif name == "vcr":
            await self._handle_vcr_command(args)

        elif name == "hida":
            if self._runtime and hasattr(self._runtime, "_last_hida_profile"):
                profile = self._runtime._last_hida_profile
                if profile is not None:
                    from llm_code.hida.engine import HidaEngine
                    engine = HidaEngine()
                    summary = engine.build_summary(profile)
                    await self._send({"type": "system", "text": f"HIDA: {summary}"})
                else:
                    hida_enabled = getattr(self._config, "hida", None) and self._config.hida.enabled
                    status = "enabled" if hida_enabled else "disabled"
                    await self._send({"type": "system", "text": f"HIDA: {status}, no classification yet"})
            else:
                await self._send({"type": "system", "text": "HIDA: not initialized"})

        elif name == "task":
            await self._handle_task_command(args)

        elif name == "swarm":
            await self._handle_swarm_command(args)

        else:
            await self._send({"type": "message", "text": f"Command /{name} not recognized. Type /help for available commands."})

    async def _handle_config_command(self, args: str) -> None:
        """Handle /config [set <key> <value> | get <key>] commands."""
        import dataclasses

        parts = args.strip().split(None, 2)
        sub = parts[0].lower() if parts else ""

        _SETTABLE = {
            "model": str,
            "temperature": float,
            "max_tokens": int,
            "max_turn_iterations": int,
            "compact_after_tokens": int,
            "timeout": float,
            "max_retries": int,
            "permission_mode": str,
        }

        if sub == "set":
            if len(parts) < 3:
                await self._send({"type": "error", "message": "Usage: /config set <key> <value>"})
                return
            key, raw_value = parts[1], parts[2]
            if key not in _SETTABLE:
                await self._send({"type": "error", "message": f"Cannot set '{key}'. Settable: {', '.join(sorted(_SETTABLE))}"})
                return
            try:
                typed_value = _SETTABLE[key](raw_value)
                self._config = dataclasses.replace(self._config, **{key: typed_value})
                if self._runtime:
                    self._runtime._config = self._config
                await self._send({"type": "message", "text": f"{key} = {typed_value}"})
            except (ValueError, TypeError) as exc:
                await self._send({"type": "error", "message": f"Invalid value for {key}: {exc}"})

        elif sub == "get":
            key = parts[1] if len(parts) > 1 else ""
            if not key:
                await self._send({"type": "error", "message": "Usage: /config get <key>"})
                return
            if hasattr(self._config, key):
                await self._send({"type": "message", "text": f"{key} = {getattr(self._config, key)}"})
            else:
                await self._send({"type": "error", "message": f"Unknown config key: {key}"})

        else:
            lines = ["Runtime Config:"]
            for f in dataclasses.fields(self._config):
                val = getattr(self._config, f.name)
                if isinstance(val, (dict, tuple, frozenset)) and not val:
                    continue
                lines.append(f"  {f.name:<28s} {val}")
            await self._send({"type": "message", "text": "\n".join(lines)})

    async def _handle_checkpoint_command(self, args: str) -> None:
        """Handle /checkpoint save|list|resume commands."""
        parts = args.strip().split(None, 1)
        sub = parts[0] if parts else ""

        try:
            from llm_code.runtime.checkpoint_recovery import CheckpointRecovery
        except ImportError:
            await self._send({"type": "error", "message": "Checkpoint module not available."})
            return

        cp_dir = self._cwd / ".llm-code" / "checkpoints"
        recovery = CheckpointRecovery(cp_dir)

        if sub == "save":
            if self._runtime and self._runtime.session:
                recovery.save_checkpoint(self._runtime.session)
                await self._send({"type": "message", "text": "Checkpoint saved."})
            else:
                await self._send({"type": "error", "message": "No active session."})
        elif sub == "list":
            checkpoints = recovery.list_checkpoints()
            if checkpoints:
                lines = [f"Checkpoints ({len(checkpoints)})"]
                for cp in checkpoints[:10]:
                    lines.append(f"  {cp.get('session_id', '?')} — {cp.get('message_count', '?')} messages")
                await self._send({"type": "message", "text": "\n".join(lines)})
            else:
                await self._send({"type": "message", "text": "No checkpoints saved."})
        elif sub == "resume":
            session_id = parts[1].strip() if len(parts) > 1 else None
            if session_id:
                session = recovery.load_checkpoint(session_id)
                if session:
                    await self._send({"type": "message", "text": f"Resumed session {session_id}"})
                else:
                    await self._send({"type": "error", "message": f"Checkpoint not found: {session_id}"})
            else:
                last = recovery.detect_last_checkpoint()
                if last:
                    await self._send({"type": "message", "text": f"Last checkpoint: {last}. Use /checkpoint resume {last}"})
                else:
                    await self._send({"type": "error", "message": "No checkpoints found."})
        else:
            await self._send({"type": "message", "text": "Usage: /checkpoint save|list|resume [session_id]"})

    async def _handle_swarm_command(self, args: str) -> None:
        """Handle /swarm coordinate <task> commands."""
        parts = args.strip().split(None, 1)
        sub = parts[0] if parts else ""
        rest = parts[1].strip() if len(parts) > 1 else ""

        if sub == "coordinate":
            if not rest:
                await self._send({"type": "error", "message": "Usage: /swarm coordinate <task>"})
                return
            if not getattr(self, "_swarm_manager", None):
                await self._send({"type": "error", "message": "Swarm not enabled. Set swarm.enabled=true in config."})
                return
            if not self._runtime:
                await self._send({"type": "error", "message": "No active session."})
                return
            await self._send({"type": "message", "text": f"Coordinating task: {rest}"})
            from llm_code.swarm.coordinator import Coordinator
            coordinator = Coordinator(
                manager=self._swarm_manager,
                provider=self._runtime._provider,
                config=self._config,
            )
            try:
                result = await coordinator.orchestrate(rest)
                await self._send({"type": "message", "text": f"Coordination result:\n{result}"})
            except Exception as exc:
                await self._send({"type": "error", "message": f"Coordination failed: {exc}"})
        else:
            await self._send({"type": "message", "text": "Usage: /swarm coordinate <task>"})

    async def _handle_task_command(self, args: str) -> None:
        """Handle /task [new|verify <id>|close <id>|list] commands."""
        parts = args.strip().split(None, 1)
        sub = parts[0] if parts else ""

        if sub in ("new", ""):
            await self._send({"type": "message", "text": "Create a new task. Ask the user for the title, plan, and goals, then use the task_plan tool."})
        elif sub == "list":
            if self._task_manager:
                tasks = self._task_manager.list_tasks(exclude_done=False)
                if not tasks:
                    await self._send({"type": "message", "text": "No tasks found."})
                else:
                    lines = ["Tasks:"]
                    for t in tasks:
                        lines.append(f"  {t.id}  [{t.status.value:8s}]  {t.title}")
                    await self._send({"type": "message", "text": "\n".join(lines)})
            else:
                await self._send({"type": "message", "text": "Task manager not available."})
        elif sub == "verify":
            task_id = parts[1].strip() if len(parts) > 1 else ""
            if not task_id:
                await self._send({"type": "message", "text": "Usage: /task verify <task_id>"})
            else:
                await self._send({"type": "message", "text": f"Verify task {task_id} using the task_verify tool."})
        elif sub == "close":
            task_id = parts[1].strip() if len(parts) > 1 else ""
            if not task_id:
                await self._send({"type": "message", "text": "Usage: /task close <task_id>"})
            else:
                await self._send({"type": "message", "text": f"Close task {task_id} using the task_close tool. Collect files modified, git diff summary, and write completion notes."})
        else:
            await self._send({"type": "message", "text": "Usage: /task [new|verify <id>|close <id>|list]"})

    async def _handle_search_command(self, args: str) -> None:
        """Handle /search <query> — search TextBlock content in conversation history."""
        if not args:
            await self._send({"type": "error", "message": "Usage: /search <query>"})
            return
        if not self._runtime:
            await self._send({"type": "message", "text": "No conversation to search."})
            return

        from llm_code.utils.search import search_messages

        query = args
        results = search_messages(list(self._runtime.session.messages), query)

        if not results:
            await self._send({"type": "message", "text": f"No results for: {query}"})
            return

        lines: list[str] = [f"Search: '{query}' — {len(results)} match(es)\n"]
        prev_idx = -1
        for r in results:
            if r.message_index != prev_idx:
                msg = self._runtime.session.messages[r.message_index]
                lines.append(f"── Message {r.message_index} ({msg.role}) ──")
                prev_idx = r.message_index
            # Surround match with ANSI-style markers the frontend can render
            before = r.line_text[:r.match_start]
            match_text = r.line_text[r.match_start:r.match_end]
            after = r.line_text[r.match_end:]
            lines.append(f"  L{r.line_number}  {before}[MATCH]{match_text}[/MATCH]{after}")

        await self._send({
            "type": "search_results",
            "query": query,
            "count": len(results),
            "text": "\n".join(lines),
            "results": [
                {
                    "message_index": r.message_index,
                    "line_number": r.line_number,
                    "line_text": r.line_text,
                    "match_start": r.match_start,
                    "match_end": r.match_end,
                }
                for r in results
            ],
        })

    async def _handle_vcr_command(self, args: str) -> None:
        """Handle /vcr start|stop|list commands."""
        sub = args.strip().split(None, 1)[0] if args.strip() else ""

        if sub == "start":
            if getattr(self, "_vcr_recorder", None) is not None:
                await self._send({"type": "message", "text": "VCR recording already active."})
                return
            import uuid
            from llm_code.runtime.vcr import VCRRecorder
            recordings_dir = self._cwd / ".llm-code" / "recordings"
            session_id = uuid.uuid4().hex[:8]
            path = recordings_dir / f"{session_id}.jsonl"
            self._vcr_recorder = VCRRecorder(path)
            if self._runtime is not None:
                self._runtime._vcr_recorder = self._vcr_recorder
            await self._send({"type": "message", "text": f"VCR recording started: {path.name}"})

        elif sub == "stop":
            recorder = getattr(self, "_vcr_recorder", None)
            if recorder is None:
                await self._send({"type": "message", "text": "No active VCR recording."})
                return
            recorder.close()
            self._vcr_recorder = None
            if self._runtime is not None:
                self._runtime._vcr_recorder = None
            await self._send({"type": "message", "text": "VCR recording stopped."})

        elif sub == "list":
            recordings_dir = self._cwd / ".llm-code" / "recordings"
            if not recordings_dir.is_dir():
                await self._send({"type": "message", "text": "No recordings found."})
                return
            files = sorted(recordings_dir.glob("*.jsonl"))
            if not files:
                await self._send({"type": "message", "text": "No recordings found."})
                return
            from llm_code.runtime.vcr import VCRPlayer
            lines = ["Recordings:"]
            for f in files:
                player = VCRPlayer(f)
                s = player.summary()
                lines.append(
                    f"  {f.name}  events={s['event_count']}  "
                    f"duration={s['duration']:.1f}s  "
                    f"tools={sum(s['tool_calls'].values())}"
                )
            await self._send({"type": "message", "text": "\n".join(lines)})

        else:
            await self._send({"type": "message", "text": "Usage: /vcr start|stop|list"})

    async def _handle_cron_command(self, args: str) -> None:
        """Handle /cron [list|add|delete <id>] commands."""
        cron_storage = getattr(self, "_cron_storage", None)
        if cron_storage is None:
            await self._send({"type": "error", "message": "Cron storage not initialized."})
            return

        sub = args.strip() if args else "list"

        if not sub or sub == "list":
            tasks = cron_storage.list_all()
            await self._send({
                "type": "cron_list",
                "tasks": [
                    {
                        "id": t.id,
                        "cron": t.cron,
                        "prompt": t.prompt,
                        "recurring": t.recurring,
                        "permanent": t.permanent,
                        "created_at": t.created_at.strftime("%Y-%m-%d %H:%M"),
                        "last_fired_at": t.last_fired_at.strftime("%Y-%m-%d %H:%M") if t.last_fired_at else None,
                    }
                    for t in tasks
                ],
            })
            if not tasks:
                await self._send({"type": "message", "text": "No scheduled tasks."})
            else:
                lines = [f"Scheduled tasks ({len(tasks)}):"]
                for t in tasks:
                    flags = []
                    if t.recurring:
                        flags.append("recurring")
                    if t.permanent:
                        flags.append("permanent")
                    flag_str = f" [{', '.join(flags)}]" if flags else ""
                    fired = f", last fired: {t.last_fired_at:%Y-%m-%d %H:%M}" if t.last_fired_at else ""
                    lines.append(f"  {t.id}  {t.cron}  \"{t.prompt}\"{flag_str}{fired}")
                await self._send({"type": "message", "text": "\n".join(lines)})

        elif sub.startswith("delete "):
            task_id = sub.split(None, 1)[1].strip()
            removed = cron_storage.remove(task_id)
            if removed:
                await self._send({"type": "message", "text": f"Deleted task {task_id}"})
            else:
                await self._send({"type": "error", "message": f"Task '{task_id}' not found"})

        elif sub == "add":
            await self._send({
                "type": "message",
                "text": (
                    "Use the cron_create tool to schedule a task:\n"
                    "  cron: '0 9 * * *'  (5-field cron expression)\n"
                    "  prompt: 'your prompt here'\n"
                    "  recurring: true/false\n"
                    "  permanent: true/false"
                ),
            })

        else:
            await self._send({"type": "message", "text": "Usage: /cron [list|add|delete <id>]"})

    async def _handle_ide_command(self, args: str) -> None:
        """Handle /ide [status|connect] commands."""
        sub = args.strip().lower()

        if sub == "status":
            ide_bridge = getattr(self, "_ide_bridge", None)
            if ide_bridge is None:
                await self._send({"type": "message", "text": "IDE integration is disabled. Set ide.enabled=true in config."})
                return
            if ide_bridge.is_connected:
                ides = ide_bridge._server.connected_ides if ide_bridge._server else []
                names = ", ".join(ide.name for ide in ides) if ides else "unknown"
                await self._send({"type": "message", "text": f"IDE connected: {names}"})
            else:
                port = ide_bridge._config.port
                await self._send({"type": "message", "text": f"IDE bridge listening on port {port}, no IDE connected."})

        elif sub == "connect":
            ide_bridge = getattr(self, "_ide_bridge", None)
            if ide_bridge is None:
                await self._send({"type": "message", "text": "IDE integration is disabled. Set ide.enabled=true in config."})
                return
            if not ide_bridge.is_enabled:
                await self._send({"type": "message", "text": "IDE integration is disabled."})
                return
            if ide_bridge._server is None:
                await ide_bridge.start()
                await self._send({"type": "message", "text": f"IDE bridge started on port {ide_bridge._server.actual_port}."})
            else:
                await self._send({"type": "message", "text": f"IDE bridge already running on port {ide_bridge._server.actual_port}."})

        else:
            await self._send({"type": "message", "text": "Usage: /ide status | /ide connect"})

    async def _show_skill_marketplace(self) -> None:
        """Show skills as text + numbered list for selection."""
        try:
            all_skills = []
            if self._skills:
                all_skills = list(self._skills.auto_skills) + list(self._skills.command_skills)

            # Check which skills are local vs from plugins
            local_skill_dir = Path.home() / ".llm-code" / "skills"
            local_names = set()
            if local_skill_dir.is_dir():
                local_names = {d.name for d in local_skill_dir.iterdir() if d.is_dir()}

            items: list[dict] = []
            for s in all_skills:
                tokens = len(s.content) // 4
                source = "local" if s.name in local_names else "plugin"
                desc = f"~{tokens} tokens"
                if source == "plugin":
                    desc += " · from plugin"
                items.append({
                    "name": s.name,
                    "description": desc,
                    "installed": True,
                    "index": len(items),
                })

            # Fetch marketplace
            try:
                market = await asyncio.wait_for(self._fetch_marketplace_skills(), timeout=8.0)
                installed_names = {s.name for s in all_skills}
                for pkg_name, desc in market:
                    if pkg_name not in installed_names:
                        items.append({
                            "name": pkg_name,
                            "description": desc,
                            "installed": False,
                            "index": len(items),
                        })
            except Exception:
                pass

            # Send via marketplace_show — limit to 50 items to avoid pipe overflow
            self._current_marketplace = {"type": "skill", "items": items}
            display_items = items  # Show all
            installed_count = sum(1 for i in display_items if i.get("installed"))
            market_count = len(display_items) - installed_count
            import time as _t
            title = f"Skills ({installed_count} installed + {market_count} available)"
            await self._send({"type": "marketplace_show", "title": title, "items": display_items, "ts": _t.time()})
        except Exception as exc:
            await self._send({"type": "error", "message": f"Error: {exc}"})

    async def _show_mcp_marketplace(self) -> None:
        """Build and send the MCP server list."""
        items: list[dict] = []
        try:
            mcp_config_path = Path.home() / ".claude" / "claude_desktop_config.json"
            if mcp_config_path.exists():
                import json as _json
                cfg = _json.loads(mcp_config_path.read_text())
                servers = cfg.get("mcpServers", {})
                for server_name in servers:
                    items.append({
                        "name": server_name,
                        "description": "configured MCP server",
                        "installed": True,
                        "index": len(items),
                    })
        except Exception:
            pass

        # Fetch npm MCP servers
        try:
            import httpx
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(
                    "https://registry.npmjs.org/-/v1/search",
                    params={"text": "mcp server modelcontextprotocol", "size": 30},
                )
                resp.raise_for_status()
                data = resp.json()
            installed_names = {it["name"] for it in items}
            for obj in data.get("objects", []):
                pkg = obj.get("package", {})
                name = pkg.get("name", "")
                desc = pkg.get("description", "")[:70]
                if ("mcp" in name.lower() or "modelcontextprotocol" in name.lower()) and name not in installed_names:
                    items.append({
                        "name": name,
                        "description": f"[npm] {desc}",
                        "installed": False,
                        "index": len(items),
                    })
        except Exception:
            pass

        # Send via React marketplace_show
        self._current_marketplace = {"type": "mcp", "items": items}
        configured = sum(1 for i in items if i.get("installed"))
        available = len(items) - configured
        title = f"MCP Servers ({configured} configured + {available} available)"
        import time as _t2
        await self._send({"type": "marketplace_show", "title": title, "items": items, "ts": _t2.time()})

    async def _show_plugin_marketplace(self) -> None:
        """Build and send the plugin list."""
        items: list[dict] = []
        try:
            from llm_code.marketplace.installer import PluginInstaller
            plugin_dir = Path.home() / ".llm-code" / "plugins"
            if plugin_dir.is_dir():
                pi = PluginInstaller(plugin_dir)
                for p in pi.list_installed():
                    items.append({
                        "name": p.manifest.name,
                        "description": f"v{p.manifest.version}",
                        "installed": True,
                        "index": len(items),
                    })
        except Exception:
            pass

        # Show loading message
        await self._send({"type": "message", "text": "Loading plugin marketplace..."})

        installed_names = {it["name"] for it in items}

        # Built-in Claude official plugin registry
        try:
            from llm_code.marketplace.builtin_registry import get_all_known_plugins
            for p in get_all_known_plugins():
                if p["name"] not in installed_names:
                    skill_info = f"{p['skills']} skills · " if p["skills"] > 0 else ""
                    items.append({
                        "name": p["name"],
                        "description": f"[Official] {skill_info}{p['desc']}",
                        "installed": False,
                        "index": len(items),
                    })
                    installed_names.add(p["name"])
        except Exception:
            pass

        # ClawHub plugins (51+)
        try:
            from llm_code.marketplace.builtin_registry import search_clawhub_plugins
            clawhub = await asyncio.wait_for(search_clawhub_plugins("", limit=30), timeout=5.0)
            for slug, desc in clawhub:
                if slug not in installed_names:
                    items.append({
                        "name": f"clawhub:{slug}",
                        "description": f"[ClawHub] {desc}",
                        "installed": False,
                        "index": len(items),
                    })
        except Exception:
            pass

        # npm plugins
        try:
            market = await asyncio.wait_for(self._fetch_npm_plugins(), timeout=5.0)
            for pkg_name, desc in market:
                if pkg_name not in installed_names:
                    items.append({
                        "name": pkg_name,
                        "description": f"[npm] {desc}",
                        "installed": False,
                        "index": len(items),
                    })
        except Exception:
            pass

        # Send via marketplace_show (React interactive selector)
        self._current_marketplace = {"type": "plugin", "items": items}
        installed_count = sum(1 for i in items if i.get("installed"))
        market_count = len(items) - installed_count
        title = f"Plugins ({installed_count} installed + {market_count} available)"
        import time as _t2
        await self._send({"type": "marketplace_show", "title": title, "items": items, "ts": _t2.time()})
        return
        # Legacy text display (kept as fallback)
        installed = [it for it in items if it.get("installed")]
        available = [it for it in items if not it.get("installed")]
        lines = [f"Plugins ({len(installed)} installed + {len(available)} available)"]
        for it in installed:
            lines.append(f"  ● {it['name']}  · {it['description']}")
        if available:
            lines.append("")
            for it in available[:15]:
                lines.append(f"  ○ {it['name']}  · {it['description']}")
            if len(available) > 15:
                lines.append(f"  ... and {len(available) - 15} more")
        lines.append("")
        lines.append("  /plugin install owner/repo  /plugin enable|disable|remove <name>")
        await self._send({"type": "message", "text": "\n".join(lines)})

    async def _handle_marketplace_selection(self, index: int) -> None:
        """Handle an item selection from the marketplace list."""
        if not self._current_marketplace:
            return

        items = self._current_marketplace.get("items", [])
        if index < 0 or index >= len(items):
            return

        item = items[index]
        self._selected_item = item
        market_type = self._current_marketplace.get("type", "")
        installed = item.get("installed", False)

        # Build actions based on marketplace type and install state
        actions: list[dict] = []
        if market_type == "skill":
            if installed:
                # Check if skill is from a plugin (can't remove individually)
                skill_dir = Path.home() / ".llm-code" / "skills" / item["name"]
                is_local = skill_dir.is_dir()
                actions = [{"id": "view", "label": "View skill content"}]
                if is_local:
                    actions.append({"id": "disable", "label": "Disable skill"})
                    actions.append({"id": "uninstall", "label": "Remove skill"})
                else:
                    actions.append({"id": "info", "label": "From plugin — manage via /plugin"})
                actions.append({"id": "cancel", "label": "Cancel"})
            else:
                actions = [
                    {"id": "install", "label": f"Install {item['name']}"},
                    {"id": "cancel", "label": "Cancel"},
                ]
        elif market_type == "plugin":
            if installed:
                actions = [
                    {"id": "disable", "label": "Disable plugin"},
                    {"id": "uninstall", "label": "Uninstall plugin"},
                    {"id": "cancel", "label": "Cancel"},
                ]
            else:
                actions = [
                    {"id": "install", "label": f"Install {item['name']}"},
                    {"id": "cancel", "label": "Cancel"},
                ]
        elif market_type == "mcp":
            actions = [
                {"id": "info", "label": "Show server info"},
                {"id": "cancel", "label": "Cancel"},
            ]
        else:
            actions = [{"id": "cancel", "label": "Cancel"}]

        await self._send({"type": "action_show", "name": item["name"], "actions": actions})

    async def _handle_marketplace_action(self, action_id: str) -> None:
        """Execute a marketplace action on the selected item."""
        if action_id == "cancel" or not self._selected_item:
            self._current_marketplace = None
            self._selected_item = None
            return

        item = self._selected_item
        market_type = (self._current_marketplace or {}).get("type", "")
        self._current_marketplace = None
        self._selected_item = None

        if action_id == "install":
            if market_type == "skill":
                await self._send({"type": "message", "text": f"Installing skill '{item['name']}' via npm…"})
                try:
                    proc = await asyncio.create_subprocess_exec(
                        "npx", "-y", "claude-skills-cli", "install", item["name"],
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    _, stderr = await proc.communicate()
                    if proc.returncode == 0:
                        await self._send({"type": "message", "text": f"Skill '{item['name']}' installed."})
                        self._reload_skills()  # Reload skills
                    else:
                        await self._send({"type": "error", "message": f"Install failed: {stderr.decode()[:200]}"})
                except Exception as exc:
                    await self._send({"type": "error", "message": f"Install failed: {exc}"})
            elif market_type == "plugin":
                name = item["name"]
                # Find repo from builtin registry
                repo = ""
                try:
                    from llm_code.marketplace.builtin_registry import get_all_known_plugins
                    registry = {p["name"]: p for p in get_all_known_plugins()}
                    repo = registry.get(name, {}).get("repo", "")
                except Exception:
                    pass

                if repo:
                    await self._send({"type": "message", "text": f"Installing plugin '{name}' from {repo}…"})
                    try:
                        dest = Path.home() / ".llm-code" / "plugins" / name
                        if dest.exists():
                            import shutil
                            shutil.rmtree(dest)
                        proc = await asyncio.create_subprocess_exec(
                            "git", "clone", "--depth", "1", f"https://github.com/{repo}.git", str(dest),
                            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                        )
                        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
                        if proc.returncode == 0:
                            from llm_code.marketplace.installer import PluginInstaller
                            PluginInstaller(Path.home() / ".llm-code" / "plugins").enable(name)
                            await self._send({"type": "message", "text": f"✓ Installed '{name}'. Restart to activate."})
                            self._reload_skills()
                        else:
                            await self._send({"type": "error", "message": f"Clone failed: {stderr.decode()[:200]}"})
                    except Exception as exc:
                        await self._send({"type": "error", "message": f"Install failed: {exc}"})
                else:
                    await self._send({"type": "message", "text": f"No install source for '{name}'. Use: /plugin install owner/repo"})

        elif action_id == "view":
            if self._skills:
                all_skills = list(self._skills.auto_skills) + list(self._skills.command_skills)
                skill = next((s for s in all_skills if s.name == item["name"]), None)
                if skill:
                    preview = skill.content[:300] + ("…" if len(skill.content) > 300 else "")
                    await self._send({"type": "message", "text": f"[{item['name']}]\n{preview}"})
                    return
            await self._send({"type": "message", "text": f"Skill '{item['name']}' content not found."})

        elif action_id == "info":
            await self._send({"type": "message", "text": f"MCP server: {item['name']}"})

        elif action_id == "uninstall":
            name = item["name"]
            if market_type == "skill":
                import shutil
                skill_dir = Path.home() / ".llm-code" / "skills" / name
                if skill_dir.is_dir():
                    shutil.rmtree(skill_dir)
                    await self._send({"type": "message", "text": f"✓ Removed skill '{name}'"})
                    self._reload_skills()  # Reload skills
                else:
                    await self._send({"type": "message", "text": f"Skill '{name}' is from a plugin — use /plugin to manage it."})
            elif market_type == "plugin":
                try:
                    from llm_code.marketplace.installer import PluginInstaller
                    pi = PluginInstaller(Path.home() / ".llm-code" / "plugins")
                    pi.uninstall(name)
                    await self._send({"type": "message", "text": f"✓ Removed plugin '{name}'"})
                    self._reload_skills()
                except Exception as exc:
                    await self._send({"type": "error", "message": f"Remove failed: {exc}"})

        elif action_id == "disable":
            name = item["name"]
            if market_type == "skill":
                marker = Path.home() / ".llm-code" / "skills" / name / ".disabled"
                marker.parent.mkdir(parents=True, exist_ok=True)
                marker.touch()
                await self._send({"type": "message", "text": f"Disabled skill '{name}'"})
                self._reload_skills()
            elif market_type == "plugin":
                try:
                    from llm_code.marketplace.installer import PluginInstaller
                    pi = PluginInstaller(Path.home() / ".llm-code" / "plugins")
                    pi.disable(name)
                    await self._send({"type": "message", "text": f"Disabled plugin '{name}'"})
                except Exception as exc:
                    await self._send({"type": "error", "message": f"Disable failed: {exc}"})

    async def _fetch_marketplace_skills(self) -> list[tuple[str, str]]:
        """Fetch skills: npm (Claude official) first, then ClawHub (44k+ community)."""
        results: list[tuple[str, str]] = []
        # 1. npm — Claude official
        try:
            import httpx
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(
                    "https://registry.npmjs.org/-/v1/search",
                    params={"text": "claude-code skill", "size": 20},
                )
                resp.raise_for_status()
                data = resp.json()
            for obj in data.get("objects", []):
                pkg = obj.get("package", {})
                name = pkg.get("name", "")
                desc = pkg.get("description", "")[:70]
                if "skill" in name.lower():
                    results.append((name, f"[npm] {desc}"))
        except Exception:
            pass
        # 2. ClawHub — 44k+ community
        try:
            from llm_code.marketplace.builtin_registry import search_clawhub_skills
            clawhub = await search_clawhub_skills("", limit=80)
            for slug, desc in clawhub:
                results.append((f"clawhub:{slug}", f"[ClawHub] {desc}"))
        except Exception:
            pass
        return results

    async def _fetch_npm_plugins(self) -> list[tuple[str, str]]:
        """Fetch plugin packages from the npm registry."""
        try:
            import httpx
        except ImportError:
            return []
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                "https://registry.npmjs.org/-/v1/search",
                params={"text": "claude-code plugin", "size": 50},
            )
            resp.raise_for_status()
            data = resp.json()
        results = []
        for obj in data.get("objects", []):
            pkg = obj.get("package", {})
            name = pkg.get("name", "")
            desc = pkg.get("description", "")[:70]
            if "plugin" in name.lower() or ("claude" in name.lower() and "plugin" in desc.lower()):
                results.append((name, desc))
        return results

    def _init_session(self) -> None:
        """Initialize the conversation runtime — same as LLMCodeCLI._init_session."""
        from llm_code.api.client import ProviderClient
        from llm_code.runtime.context import ProjectContext
        from llm_code.runtime.conversation import ConversationRuntime
        from llm_code.runtime.hooks import HookRunner
        from llm_code.runtime.permissions import PermissionMode, PermissionPolicy
        from llm_code.runtime.prompt import SystemPromptBuilder
        from llm_code.runtime.session import Session
        from llm_code.tools.registry import ToolRegistry

        # Build provider
        api_key = os.environ.get(self._config.provider_api_key_env, "")
        resolved_model = resolve_model(
            self._config.model, custom_aliases=self._config.model_aliases
        )
        self._cost_tracker = CostTracker(
            model=resolved_model,
            custom_pricing=self._config.pricing or None,
            max_budget_usd=self._config.max_budget_usd,
        )
        provider = ProviderClient.from_model(
            model=resolved_model,
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
        from llm_code.tools.notebook_read import NotebookReadTool
        from llm_code.tools.notebook_edit import NotebookEditTool

        registry = ToolRegistry()
        for cls in (ReadFileTool, WriteFileTool, EditFileTool, BashTool, GlobSearchTool, GrepSearchTool, NotebookReadTool, NotebookEditTool):
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

        # AgentTool
        try:
            from llm_code.tools.agent import AgentTool
            if registry.get("agent") is None:
                registry.register(AgentTool(
                    runtime_factory=None, max_depth=3, current_depth=0,
                ))
        except (ImportError, ValueError):
            pass

        # Deferred tool manager + ToolSearchTool
        from llm_code.tools.deferred import DeferredToolManager
        from llm_code.tools.tool_search import ToolSearchTool
        self._deferred_tool_manager = DeferredToolManager()
        try:
            registry.register(ToolSearchTool(self._deferred_tool_manager))
        except ValueError:
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

        # Memory
        try:
            from llm_code.runtime.memory import MemoryStore
            self._memory = MemoryStore(Path.home() / ".llm-code" / "memory", self._cwd)
        except Exception:
            self._memory = None

        # Register memory tools
        if self._memory:
            try:
                from llm_code.tools.memory_tools import MemoryStoreTool, MemoryRecallTool, MemoryListTool
                for tool_cls in (MemoryStoreTool, MemoryRecallTool, MemoryListTool):
                    try:
                        registry.register(tool_cls(self._memory))
                    except ValueError:
                        pass
            except Exception:
                pass

        # Register cron tools
        try:
            from llm_code.cron.storage import CronStorage
            from llm_code.tools.cron_create import CronCreateTool
            from llm_code.tools.cron_list import CronListTool
            from llm_code.tools.cron_delete import CronDeleteTool
            cron_storage = CronStorage(self._cwd / ".llm-code" / "scheduled_tasks.json")
            self._cron_storage = cron_storage
            for tool in (CronCreateTool(cron_storage), CronListTool(cron_storage), CronDeleteTool(cron_storage)):
                try:
                    registry.register(tool)
                except ValueError:
                    pass
        except Exception:
            self._cron_storage = None

        # Register swarm tools
        self._swarm_manager = None
        try:
            if self._config.swarm.enabled:
                from llm_code.swarm.manager import SwarmManager
                from llm_code.tools.swarm_create import SwarmCreateTool
                from llm_code.tools.swarm_list import SwarmListTool
                from llm_code.tools.swarm_message import SwarmMessageTool
                from llm_code.tools.swarm_delete import SwarmDeleteTool
                from llm_code.tools.coordinator_tool import CoordinatorTool
                from llm_code.swarm.coordinator import Coordinator

                swarm_mgr = SwarmManager(
                    swarm_dir=self._cwd / ".llm-code" / "swarm",
                    max_members=self._config.swarm.max_members,
                    backend_preference=self._config.swarm.backend,
                )
                self._swarm_manager = swarm_mgr
                for tool in (
                    SwarmCreateTool(swarm_mgr),
                    SwarmListTool(swarm_mgr),
                    SwarmMessageTool(swarm_mgr),
                    SwarmDeleteTool(swarm_mgr),
                ):
                    try:
                        registry.register(tool)
                    except ValueError:
                        pass
                # Store coordinator classes for lazy registration after runtime init
                self._coordinator_class = Coordinator
                self._coordinator_tool_class = CoordinatorTool
        except Exception:
            self._swarm_manager = None

        # Register task lifecycle tools
        self._task_manager = None
        try:
            from llm_code.task.manager import TaskLifecycleManager
            from llm_code.task.verifier import Verifier
            from llm_code.task.diagnostics import DiagnosticsEngine
            from llm_code.tools.task_plan import TaskPlanTool
            from llm_code.tools.task_verify import TaskVerifyTool
            from llm_code.tools.task_close import TaskCloseTool

            task_dir = self._cwd / ".llm-code" / "tasks"
            diag_dir = self._cwd / ".llm-code" / "diagnostics"
            task_mgr = TaskLifecycleManager(task_dir=task_dir)
            task_verifier = Verifier(cwd=self._cwd)
            task_diagnostics = DiagnosticsEngine(diagnostics_dir=diag_dir)
            self._task_manager = task_mgr

            sid = session.id if session else ""

            for tool in (
                TaskPlanTool(task_mgr, session_id=sid),
                TaskVerifyTool(task_mgr, task_verifier, task_diagnostics),
                TaskCloseTool(task_mgr),
            ):
                try:
                    registry.register(tool)
                except ValueError:
                    pass
        except Exception:
            self._task_manager = None

        # Register computer-use tools (only when enabled)
        if self._config.computer_use.enabled:
            try:
                from llm_code.tools.computer_use_tools import (
                    ScreenshotTool, MouseClickTool, KeyboardTypeTool,
                    KeyPressTool, ScrollTool, MouseDragTool,
                )
                cu_config = self._config.computer_use
                for tool in (
                    ScreenshotTool(cu_config), MouseClickTool(cu_config),
                    KeyboardTypeTool(cu_config), KeyPressTool(cu_config),
                    ScrollTool(cu_config), MouseDragTool(cu_config),
                ):
                    try:
                        registry.register(tool)
                    except ValueError:
                        pass
            except ImportError:
                pass

        # Register IDE tools if enabled
        if self._config.ide.enabled:
            try:
                from llm_code.ide.bridge import IDEBridge
                from llm_code.tools.ide_open import IDEOpenTool
                from llm_code.tools.ide_diagnostics import IDEDiagnosticsTool
                from llm_code.tools.ide_selection import IDESelectionTool

                self._ide_bridge = IDEBridge(self._config.ide)
                for tool in (
                    IDEOpenTool(self._ide_bridge),
                    IDEDiagnosticsTool(self._ide_bridge),
                    IDESelectionTool(self._ide_bridge),
                ):
                    try:
                        registry.register(tool)
                    except ValueError:
                        pass
            except ImportError:
                self._ide_bridge = None
        else:
            self._ide_bridge = None

        # Store checkpoint manager
        self._checkpoint_mgr = checkpoint_mgr

        # Recovery checkpoint (session state persistence)
        recovery_checkpoint = None
        try:
            from llm_code.runtime.checkpoint_recovery import CheckpointRecovery
            recovery_checkpoint = CheckpointRecovery(Path.home() / ".llm-code" / "checkpoints")
        except Exception:
            pass

        # Build project index
        self._project_index = None
        try:
            from llm_code.runtime.indexer import ProjectIndexer
            self._project_index = ProjectIndexer(self._cwd).build_index()
        except Exception:
            pass

        # M5: LSP tools
        self._lsp_manager = None
        if self._config.lsp_servers or self._config.lsp_auto_detect:
            try:
                from llm_code.lsp.manager import LspServerManager
                from llm_code.lsp.tools import LspGotoDefinitionTool, LspFindReferencesTool, LspDiagnosticsTool
                self._lsp_manager = LspServerManager()
                for tool in (
                    LspGotoDefinitionTool(self._lsp_manager),
                    LspFindReferencesTool(self._lsp_manager),
                    LspDiagnosticsTool(self._lsp_manager),
                ):
                    try:
                        registry.register(tool)
                    except ValueError:
                        pass
            except ImportError:
                pass

        # M4: Telemetry
        telemetry = None
        if getattr(self._config, "telemetry", None) and self._config.telemetry.enabled:
            try:
                from llm_code.runtime.telemetry import Telemetry, TelemetryConfig
                telemetry = Telemetry(TelemetryConfig(
                    enabled=True,
                    endpoint=self._config.telemetry.endpoint,
                    service_name=self._config.telemetry.service_name,
                ))
            except Exception:
                pass

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
            recovery_checkpoint=recovery_checkpoint,
            cost_tracker=self._cost_tracker,
            deferred_tool_manager=self._deferred_tool_manager,
            telemetry=telemetry,
            skills=self._skills,
            memory_store=self._memory,
            task_manager=self._task_manager,
            project_index=self._project_index,
        )
        self._tool_reg = registry

    async def _init_mcp_servers(self) -> None:
        """Start MCP servers and register their tools."""
        if not self._config.mcp_servers:
            self._mcp_manager = None
            return
        try:
            from llm_code.mcp.manager import McpServerManager
            from llm_code.mcp.types import McpServerConfig

            manager = McpServerManager()
            configs: dict[str, McpServerConfig] = {}
            for name, raw in self._config.mcp_servers.items():
                if isinstance(raw, dict):
                    configs[name] = McpServerConfig(
                        command=raw.get("command"),
                        args=tuple(raw.get("args", ())),
                        env=raw.get("env"),
                        transport_type=raw.get("transport_type", "stdio"),
                        url=raw.get("url"),
                        headers=raw.get("headers"),
                    )
            await manager.start_all(configs)
            registered = await manager.register_all_tools(self._tool_reg)
            self._mcp_manager = manager
            if self._runtime is not None:
                self._runtime._mcp_manager = manager
            if registered:
                await self._send({"type": "system", "text": f"MCP: {len(configs)} server(s), {registered} tool(s) registered"})
        except Exception as exc:
            logger.warning("MCP initialization failed: %s", exc)
            self._mcp_manager = None

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

    def _fire_hook(self, event: str, context: dict | None = None) -> None:
        """Fire a hook event via the runtime's hook runner."""
        if self._runtime is not None and hasattr(self._runtime, "_hooks") and hasattr(self._runtime._hooks, "fire"):
            try:
                self._runtime._hooks.fire(event, context or {})
            except Exception:
                pass

    async def _auto_save_on_exit(self) -> None:
        """Auto-save session + generate summary on exit."""
        if not self._runtime or not self._runtime.session:
            return

        print("\n[dim]Saving session...[/]", file=sys.stderr)

        # 1. Save session to disk
        try:
            from llm_code.runtime.session import SessionManager
            sm = SessionManager(Path.home() / ".llm-code" / "sessions")
            sm.save(self._runtime.session)
            self._fire_hook("session_save", {})
        except Exception:
            pass

        # 2. Generate summary and save to memory
        if self._memory and len(self._runtime.session.messages) > 2:
            try:
                # Build a simple summary from the conversation
                messages = self._runtime.session.messages
                topics = []
                for msg in messages:
                    for block in msg.content:
                        if hasattr(block, 'text') and msg.role == 'user':
                            text = block.text[:100]
                            if text and not text.startswith('/'):
                                topics.append(text)

                if topics:
                    summary = "Session topics: " + "; ".join(topics[:5])
                    self._memory.save_session_summary(summary)
                    print(f"Session saved with {len(topics)} topics.", file=sys.stderr)
            except Exception:
                pass

        # 3. Fire DreamTask consolidation (non-blocking)
        self._fire_hook("session_dream", {})
        self._fire_hook("session_end", {})
        if self._memory and self._runtime and self._runtime.session:
            try:
                import asyncio as _asyncio
                from llm_code.runtime.dream import DreamTask

                dream = DreamTask()
                _asyncio.create_task(
                    dream.consolidate(
                        self._runtime.session,
                        self._memory,
                        self._runtime._provider,
                        self._config,
                    )
                )
            except Exception:
                pass

        # Stop all swarm members
        if getattr(self, "_swarm_manager", None) is not None:
            try:
                await self._swarm_manager.stop_all()
            except Exception:
                pass

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

    def _reload_skills(self) -> None:
        """Lightweight reload of skills only — does not rebuild runtime."""
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
            pass
