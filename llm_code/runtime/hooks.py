"""Hook runner supporting 6 types, 24 events, glob event matching, per-hook timeout/on_error."""
from __future__ import annotations

import fnmatch
import json
import os
import subprocess
from dataclasses import dataclass, field

from llm_code.runtime.config import HookConfig
from llm_code.tools.base import ToolResult

# Legacy global fallback timeout (used only when hook.timeout is not set, kept for compat)
HOOK_TIMEOUT = 10.0

# All 24 supported event names across 6 types.
# Canonical names used for glob matching with event patterns like "tool.*", "session.*", "*".
#
# Group prefixes are derived from the event name by taking the segment before "_" or the
# shorthand group word that appears in the category comment below.
#
# tool     -> pre_tool_use, post_tool_use, tool_error, tool_denied
# command  -> pre_command, post_command, command_error
# prompt   -> prompt_submit, prompt_compile, prompt_cache_hit, prompt_cache_miss
# agent    -> agent_spawn, agent_complete, agent_error, agent_message
# session  -> session_start, session_end, session_save, session_compact, session_dream
# http     -> http_request, http_response, http_error, http_retry, http_fallback

# Mapping from event name to its dot-prefixed canonical group name used for glob matching.
# E.g. "pre_tool_use" -> "tool.pre_tool_use", so pattern "tool.*" matches.
_EVENT_GROUP: dict[str, str] = {
    # tool
    "pre_tool_use": "tool.pre_tool_use",
    "post_tool_use": "tool.post_tool_use",
    "tool_error": "tool.tool_error",
    "tool_denied": "tool.tool_denied",
    # command
    "pre_command": "command.pre_command",
    "post_command": "command.post_command",
    "command_error": "command.command_error",
    # prompt
    "prompt_submit": "prompt.prompt_submit",
    "prompt_compile": "prompt.prompt_compile",
    "prompt_cache_hit": "prompt.prompt_cache_hit",
    "prompt_cache_miss": "prompt.prompt_cache_miss",
    # agent
    "agent_spawn": "agent.agent_spawn",
    "agent_complete": "agent.agent_complete",
    "agent_error": "agent.agent_error",
    "agent_message": "agent.agent_message",
    # session
    "session_start": "session.session_start",
    "session_end": "session.session_end",
    "session_save": "session.session_save",
    "session_compact": "session.session_compact",
    "session_dream": "session.session_dream",
    # http
    "http_request": "http.http_request",
    "http_response": "http.http_response",
    "http_error": "http.http_error",
    "http_retry": "http.http_retry",
    "http_fallback": "http.http_fallback",
}


def _event_matches(pattern: str, event: str) -> bool:
    """Return True if *pattern* matches *event*.

    Matching rules (in order):
    1. "*" matches any event.
    2. Pattern containing "." is matched against the dotted form "group.event"
       (e.g. "tool.*" matches "tool.pre_tool_use").
    3. Exact match (original event name, no dots).
    """
    if pattern == "*":
        return True
    dotted = _EVENT_GROUP.get(event, event)
    if "." in pattern:
        return fnmatch.fnmatch(dotted, pattern)
    return pattern == event


def _build_env(event: str, context: dict) -> dict[str, str]:
    """Build the environment mapping to pass to a hook process."""
    env = {**os.environ}
    env["HOOK_EVENT"] = event
    env["HOOK_TOOL_NAME"] = context.get("tool_name", "")
    env["HOOK_TOOL_INPUT"] = context.get("tool_input", "")
    env["HOOK_TOOL_OUTPUT"] = context.get("tool_output", "")
    env["HOOK_SESSION_ID"] = context.get("session_id", "")
    env["HOOK_AGENT_ID"] = context.get("agent_id", "")
    env["HOOK_HTTP_URL"] = context.get("url", "")
    env["HOOK_HTTP_STATUS"] = context.get("status", "")
    env["HOOK_COMMAND"] = context.get("command", "")
    return env


@dataclass
class HookOutcome:
    denied: bool = False
    messages: list[str] = field(default_factory=list)


class HookRunner:
    def __init__(self, hooks: tuple[HookConfig, ...] = ()) -> None:
        self._hooks = hooks

    # ------------------------------------------------------------------
    # Public generic entry point
    # ------------------------------------------------------------------

    def fire(self, event: str, context: dict) -> HookOutcome:
        """Fire all hooks whose event pattern matches *event*.

        *context* is a plain dict carrying optional keys:
            tool_name, tool_input, tool_output,
            session_id, agent_id, url, status, command
        """
        env = _build_env(event, context)
        outcome = HookOutcome()

        for hook in self._hooks:
            if not _event_matches(hook.event, event):
                continue

            hook_outcome = self._run_single_hook(hook, env)
            if hook_outcome.denied:
                return hook_outcome  # stop on first deny
            outcome.messages.extend(hook_outcome.messages)

        return outcome

    # ------------------------------------------------------------------
    # Legacy helpers (backwards compat)
    # ------------------------------------------------------------------

    def pre_tool_use(self, tool_name: str, args: dict) -> HookOutcome:
        """Run all pre_tool_use hooks that match tool_name."""
        context = {
            "tool_name": tool_name,
            "tool_input": json.dumps(args),
            "tool_output": "",
        }
        # Build env with legacy keys too
        env = _build_env("pre_tool_use", context)
        env["HOOK_TOOL_IS_ERROR"] = "false"
        return self._run_hooks_with_env("pre_tool_use", tool_name, env)

    def post_tool_use(self, tool_name: str, args: dict, result: ToolResult) -> HookOutcome:
        """Run all post_tool_use hooks that match tool_name."""
        context = {
            "tool_name": tool_name,
            "tool_input": json.dumps(args),
            "tool_output": result.output,
        }
        env = _build_env("post_tool_use", context)
        env["HOOK_TOOL_IS_ERROR"] = "true" if result.is_error else "false"
        return self._run_hooks_with_env("post_tool_use", tool_name, env)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _run_hooks_with_env(
        self, event: str, tool_name: str, env: dict[str, str]
    ) -> HookOutcome:
        """Execute matching hooks using a pre-built env (legacy path)."""
        outcome = HookOutcome()

        for hook in self._hooks:
            if not _event_matches(hook.event, event):
                continue
            if not fnmatch.fnmatch(tool_name, hook.tool_pattern):
                continue

            hook_outcome = self._run_single_hook(hook, env)
            if hook_outcome.denied:
                return hook_outcome
            outcome.messages.extend(hook_outcome.messages)

        return outcome

    def _run_single_hook(self, hook: HookConfig, env: dict[str, str]) -> HookOutcome:
        """Run one shell command and interpret its exit code respecting hook.on_error."""
        timeout = getattr(hook, "timeout", HOOK_TIMEOUT)
        on_error = getattr(hook, "on_error", "warn")

        try:
            proc = subprocess.run(
                hook.command,
                shell=True,
                env=env,
                timeout=timeout,
                capture_output=True,
                text=True,
            )
        except subprocess.TimeoutExpired:
            msg = f"Hook timed out after {timeout}s: {hook.command}"
            denied = hook.on_error == "deny"
            return HookOutcome(denied=denied, messages=[msg])
        except Exception as exc:
            denied = hook.on_error == "deny"
            return HookOutcome(denied=denied, messages=[f"Hook error: {exc}"])

        if proc.returncode == 0:
            return HookOutcome(denied=False)

        if proc.returncode == 2:
            msg = proc.stdout.strip() or proc.stderr.strip() or "Hook denied tool use"
            return HookOutcome(denied=True, messages=[msg])

        # Non-zero, non-2: apply on_error policy
        msg = (
            proc.stdout.strip()
            or proc.stderr.strip()
            or f"Hook exited with code {proc.returncode}: {hook.command}"
        )
        if on_error == "deny":
            return HookOutcome(denied=True, messages=[msg])
        elif on_error == "ignore":
            return HookOutcome(denied=False, messages=[])
        else:  # "warn" (default)
            return HookOutcome(denied=False, messages=[msg])
