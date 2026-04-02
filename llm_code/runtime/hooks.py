"""Hook runner for pre/post tool use and on_stop events."""
from __future__ import annotations

import fnmatch
import json
import subprocess
from dataclasses import dataclass, field

from llm_code.runtime.config import HookConfig
from llm_code.tools.base import ToolResult

HOOK_TIMEOUT = 10.0  # seconds


@dataclass
class HookOutcome:
    denied: bool = False
    messages: list[str] = field(default_factory=list)


class HookRunner:
    def __init__(self, hooks: tuple[HookConfig, ...] = ()) -> None:
        self._hooks = hooks

    def pre_tool_use(self, tool_name: str, args: dict) -> HookOutcome:
        """Run all pre_tool_use hooks that match tool_name."""
        env_vars = {
            "HOOK_EVENT": "pre_tool_use",
            "HOOK_TOOL_NAME": tool_name,
            "HOOK_TOOL_INPUT": json.dumps(args),
            "HOOK_TOOL_OUTPUT": "",
            "HOOK_TOOL_IS_ERROR": "false",
        }
        return self._run_hooks("pre_tool_use", tool_name, env_vars)

    def post_tool_use(self, tool_name: str, args: dict, result: ToolResult) -> HookOutcome:
        """Run all post_tool_use hooks that match tool_name."""
        env_vars = {
            "HOOK_EVENT": "post_tool_use",
            "HOOK_TOOL_NAME": tool_name,
            "HOOK_TOOL_INPUT": json.dumps(args),
            "HOOK_TOOL_OUTPUT": result.output,
            "HOOK_TOOL_IS_ERROR": "true" if result.is_error else "false",
        }
        return self._run_hooks("post_tool_use", tool_name, env_vars)

    def _run_hooks(self, event: str, tool_name: str, env_vars: dict[str, str]) -> HookOutcome:
        """Execute matching hooks, returning combined outcome."""
        import os

        outcome = HookOutcome()
        env = {**os.environ, **env_vars}

        for hook in self._hooks:
            if hook.event != event:
                continue
            if not fnmatch.fnmatch(tool_name, hook.tool_pattern):
                continue

            hook_outcome = self._run_single_hook(hook.command, env)
            if hook_outcome.denied:
                return hook_outcome  # stop on first deny
            outcome.messages.extend(hook_outcome.messages)

        return outcome

    def _run_single_hook(self, command: str, env: dict[str, str]) -> HookOutcome:
        """Run one shell command and interpret its exit code."""
        try:
            proc = subprocess.run(
                command,
                shell=True,
                env=env,
                timeout=HOOK_TIMEOUT,
                capture_output=True,
                text=True,
            )
        except subprocess.TimeoutExpired:
            return HookOutcome(
                denied=False,
                messages=[f"Hook timed out after {HOOK_TIMEOUT}s: {command}"],
            )
        except Exception as exc:
            return HookOutcome(
                denied=False,
                messages=[f"Hook error: {exc}"],
            )

        if proc.returncode == 0:
            return HookOutcome(denied=False)
        elif proc.returncode == 2:
            msg = proc.stdout.strip() or proc.stderr.strip() or "Hook denied tool use"
            return HookOutcome(denied=True, messages=[msg])
        else:
            msg = (
                proc.stdout.strip()
                or proc.stderr.strip()
                or f"Hook exited with code {proc.returncode}: {command}"
            )
            return HookOutcome(denied=False, messages=[msg])
