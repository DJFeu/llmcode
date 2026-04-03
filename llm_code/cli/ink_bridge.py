"""Python ↔ Ink IPC bridge for llm-code."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

from llm_code.runtime.config import RuntimeConfig
from llm_code.runtime.cost_tracker import CostTracker
from llm_code.runtime.model_aliases import resolve_model


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
        self._cost_tracker = CostTracker(model=self._config.model, custom_pricing=self._config.pricing or None)

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
            try:
                line = json.dumps(msg, ensure_ascii=False) + "\n"
                self._ink_process.stdin.write(line.encode())
                await asyncio.wait_for(self._ink_process.stdin.drain(), timeout=3.0)
            except Exception as e:
                import sys
                print(f"[send error: {e}]", file=sys.stderr)

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
            await self._send({"type": "message", "text": self._cost_tracker.format_cost()})

        elif name == "skill":
            await self._show_skill_marketplace()

        elif name == "mcp":
            await self._show_mcp_marketplace()

        elif name == "plugin":
            await self._show_plugin_marketplace()

        elif name == "cancel":
            # Cancel current generation
            # TODO: implement actual cancellation
            await self._send({"type": "thinking_stop", "elapsed": 0, "tokens": 0})
            await self._send({"type": "message", "text": "(cancelled)"})

        else:
            await self._send({"type": "message", "text": f"Command /{name} not recognized. Type /help for available commands."})

    async def _show_skill_marketplace(self) -> None:
        """Build and send the skill marketplace list — local first, then async fetch."""
        all_skills = []
        if self._skills:
            all_skills = list(self._skills.auto_skills) + list(self._skills.command_skills)

        items: list[dict] = []
        for s in all_skills:
            tokens = len(s.content) // 4
            items.append({
                "name": s.name,
                "description": f"~{tokens} tokens",
                "installed": True,
                "index": len(items),
            })

        # Fetch marketplace — but always show results even if fetch fails
        market: list[tuple[str, str]] = []
        try:
            market = await asyncio.wait_for(self._fetch_marketplace_skills(), timeout=8.0)
        except Exception:
            pass

        installed_names = {s.name for s in all_skills}
        for pkg_name, desc in market:
            if pkg_name not in installed_names:
                items.append({
                    "name": pkg_name,
                    "description": desc,
                    "installed": False,
                    "index": len(items),
                })

        # Always send — even if only local skills
        self._current_marketplace = {"type": "skill", "items": items}
        installed_count = sum(1 for i in items if i.get("installed"))
        market_count = len(items) - installed_count
        title = f"Skills ({installed_count} installed"
        if market_count > 0:
            title += f" + {market_count} available"
        title += ")"
        await self._send({"type": "marketplace_show", "title": title, "items": items})

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

        self._current_marketplace = {"type": "mcp", "items": items}
        title = f"MCP Servers ({len(items)})" if items else "MCP Servers (none configured)"
        await self._send({"type": "marketplace_show", "title": title, "items": items})

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

        # Send full list
        self._current_marketplace = {"type": "plugin", "items": items}
        installed_count = sum(1 for i in items if i.get("installed"))
        market_count = len(items) - installed_count
        title = f"Plugins ({installed_count} installed"
        if market_count > 0:
            title += f" + {market_count} available"
        title += ")"
        await self._send({"type": "marketplace_show", "title": title, "items": items})

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
                actions = [
                    {"id": "view", "label": "View skill content"},
                    {"id": "uninstall", "label": "Remove skill"},
                    {"id": "cancel", "label": "Cancel"},
                ]
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
                await self._send({"type": "message", "text": f"Installing plugin '{item['name']}' via npm…"})
                try:
                    from llm_code.marketplace.installer import PluginInstaller
                    plugin_dir = Path.home() / ".llm-code" / "plugins"
                    plugin_dir.mkdir(parents=True, exist_ok=True)
                    pi = PluginInstaller(plugin_dir)
                    await asyncio.get_event_loop().run_in_executor(None, pi.install, item["name"])
                    await self._send({"type": "message", "text": f"Plugin '{item['name']}' installed."})
                    self._reload_skills()
                except Exception as exc:
                    await self._send({"type": "error", "message": f"Plugin install failed: {exc}"})

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
                    await self._send({"type": "message", "text": f"Skill '{name}' not found on disk."})
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
            clawhub = await search_clawhub_skills("", limit=40)
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
        resolved_model = resolve_model(
            self._config.model, custom_aliases=self._config.model_aliases
        )
        self._cost_tracker = CostTracker(model=resolved_model, custom_pricing=self._config.pricing or None)
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
