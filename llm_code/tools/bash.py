"""BashTool — execute shell commands with timeout and safety checks."""
from __future__ import annotations

import re
import subprocess

from llm_code.tools.base import PermissionLevel, Tool, ToolResult

# Patterns that indicate destructive/dangerous commands
_DANGEROUS_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\brm\s+-[^\s]*r[^\s]*\s+(\/|~|\$HOME|\*)", re.IGNORECASE),
    re.compile(r"\brm\s+-rf\b", re.IGNORECASE),
    re.compile(r":\s*\(\)\s*\{.*\}", re.IGNORECASE),  # fork bomb
    re.compile(r"\bdd\s+if=.*of=/dev/", re.IGNORECASE),
    re.compile(r">\s*/dev/sd[a-z]", re.IGNORECASE),
    re.compile(r"\bmkfs\b", re.IGNORECASE),
    re.compile(r"chmod\s+-R\s+777\s+/", re.IGNORECASE),
]


def _is_dangerous(command: str) -> bool:
    return any(p.search(command) for p in _DANGEROUS_PATTERNS)


class BashTool(Tool):
    def __init__(self, default_timeout: int = 30, max_output: int = 8000) -> None:
        self._default_timeout = default_timeout
        self._max_output = max_output

    @property
    def name(self) -> str:
        return "bash"

    @property
    def description(self) -> str:
        return "Execute a bash shell command and return its output."

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to execute"},
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds (default 30)",
                    "default": 30,
                },
            },
            "required": ["command"],
        }

    @property
    def required_permission(self) -> PermissionLevel:
        return PermissionLevel.FULL_ACCESS

    def execute(self, args: dict) -> ToolResult:
        command: str = args["command"]
        timeout: int = int(args.get("timeout", self._default_timeout))

        if _is_dangerous(command):
            return ToolResult(
                output=f"Dangerous command blocked: {command}",
                is_error=True,
                metadata={"dangerous": True},
            )

        try:
            proc = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            output = proc.stdout
            if proc.stderr:
                output = (output + proc.stderr).strip()
            else:
                output = output.rstrip("\n")

            if len(output) > self._max_output:
                truncated = output[: self._max_output]
                output = truncated + f"\n... [output truncated at {self._max_output} chars]"

            is_error = proc.returncode != 0
            return ToolResult(output=output, is_error=is_error)

        except subprocess.TimeoutExpired:
            return ToolResult(
                output=f"Command timed out after {timeout}s: {command}",
                is_error=True,
            )
        except Exception as exc:
            return ToolResult(output=f"Error executing command: {exc}", is_error=True)
