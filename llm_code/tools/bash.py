"""BashTool — execute shell commands with timeout and safety checks."""
from __future__ import annotations

import re
import select
import subprocess
from typing import Callable

from pydantic import BaseModel

from llm_code.tools.base import PermissionLevel, Tool, ToolProgress, ToolResult

# ---------------------------------------------------------------------------
# Input model
# ---------------------------------------------------------------------------


class BashInput(BaseModel):
    command: str
    timeout: int = 30


# ---------------------------------------------------------------------------
# Pattern lists
# ---------------------------------------------------------------------------

# Read-only command prefixes / patterns
_READ_ONLY_PATTERNS: list[re.Pattern[str]] = [
    # Basic file inspection
    re.compile(r"^\s*(ls|cat|head|tail|wc|echo|pwd|whoami|date|uname|which|type|file|stat)\b"),
    # Search tools
    re.compile(r"^\s*(grep|rg|find|fd|ag|ack)\b"),
    # Git read-only subcommands
    re.compile(r"^\s*git\s+(status|log|diff|show|branch|remote|tag)\b"),
    # Python/node one-liners that only print
    re.compile(r'^\s*python\s+-c\s+["\'].*print', re.IGNORECASE),
    re.compile(r'^\s*node\s+-e\s+["\'].*console\.log', re.IGNORECASE),
    # System info
    re.compile(r"^\s*(env|printenv|id|hostname|df|du|free|uptime|ps)\b"),
]

# Truly dangerous patterns (blocked in execute() — irreversible, catastrophic)
_TRULY_DANGEROUS_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\brm\s+-[^\s]*r[^\s]*\s+(\/|~|\$HOME|\*)", re.IGNORECASE),
    re.compile(r"\brm\s+-rf\b", re.IGNORECASE),
    re.compile(r":\s*\(\)\s*\{.*\}", re.IGNORECASE),  # fork bomb
    re.compile(r"\bdd\s+if=.*of=/dev/", re.IGNORECASE),
    re.compile(r">\s*/dev/sd[a-z]", re.IGNORECASE),
    re.compile(r"\bmkfs\b", re.IGNORECASE),
    re.compile(r"chmod\s+-R\s+777\s+/", re.IGNORECASE),
]


def _is_truly_dangerous(command: str) -> bool:
    return any(p.search(command) for p in _TRULY_DANGEROUS_PATTERNS)


# Destructive patterns
_DESTRUCTIVE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\brm\s+(-[^\s]*r|-rf?)\b", re.IGNORECASE),
    re.compile(r"\bgit\s+(push|reset|rebase|merge|clean)\b", re.IGNORECASE),
    re.compile(r"\bdrop\s+table\b", re.IGNORECASE),
    re.compile(r"\btruncate\b", re.IGNORECASE),
    re.compile(r"\bmkfs\b", re.IGNORECASE),
    re.compile(r"\bdd\s+if=", re.IGNORECASE),
]


# ---------------------------------------------------------------------------
# BashTool
# ---------------------------------------------------------------------------


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

    @property
    def input_model(self) -> type[BashInput]:
        return BashInput

    def is_read_only(self, args: dict) -> bool:
        command: str = args.get("command", "")
        return any(p.search(command) for p in _READ_ONLY_PATTERNS)

    def is_destructive(self, args: dict) -> bool:
        command: str = args.get("command", "")
        return any(p.search(command) for p in _DESTRUCTIVE_PATTERNS)

    def is_concurrency_safe(self, args: dict) -> bool:
        return self.is_read_only(args)

    def execute(self, args: dict) -> ToolResult:
        command: str = args["command"]
        timeout: int = int(args.get("timeout", self._default_timeout))

        # Block truly dangerous commands (irreversible, high-risk)
        if _is_truly_dangerous(command):
            return ToolResult(
                output=f"Dangerous command blocked: {command}",
                is_error=True,
                metadata={"dangerous": True},
            )

        return self._run(command, timeout)

    def execute_with_progress(
        self,
        args: dict,
        on_progress: Callable[[ToolProgress], None],
    ) -> ToolResult:
        command: str = args["command"]
        timeout: int = int(args.get("timeout", self._default_timeout))

        try:
            proc = subprocess.Popen(
                command,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        except Exception as exc:
            return ToolResult(output=f"Error starting command: {exc}", is_error=True)

        output_chunks: list[str] = []
        last_line = ""
        deadline = __import__("time").monotonic() + timeout

        try:
            while True:
                remaining = deadline - __import__("time").monotonic()
                if remaining <= 0:
                    proc.kill()
                    proc.wait()
                    return ToolResult(
                        output=f"Command timed out after {timeout}s: {command}",
                        is_error=True,
                    )

                # Poll for output with up to 1-second wait
                wait = min(remaining, 1.0)
                readable, _, _ = select.select([proc.stdout], [], [], wait)

                if readable:
                    chunk = proc.stdout.read(4096)  # type: ignore[union-attr]
                    if chunk:
                        output_chunks.append(chunk)
                        lines = chunk.splitlines()
                        if lines:
                            last_line = lines[-1]
                        on_progress(
                            ToolProgress(
                                tool_name=self.name,
                                message=last_line or "Running...",
                            )
                        )

                # Check if process has finished
                if proc.poll() is not None:
                    # Drain remaining stdout
                    remaining_out = proc.stdout.read()  # type: ignore[union-attr]
                    if remaining_out:
                        output_chunks.append(remaining_out)
                    break

        except Exception as exc:
            proc.kill()
            proc.wait()
            return ToolResult(output=f"Error executing command: {exc}", is_error=True)

        # Also capture stderr
        stderr_out = proc.stderr.read()  # type: ignore[union-attr]

        output = "".join(output_chunks)
        if stderr_out:
            output = (output + stderr_out).strip()
        else:
            output = output.rstrip("\n")

        if len(output) > self._max_output:
            output = output[: self._max_output] + f"\n... [output truncated at {self._max_output} chars]"

        is_error = proc.returncode != 0
        return ToolResult(output=output, is_error=is_error)

    def _run(self, command: str, timeout: int) -> ToolResult:
        """Simple blocking run (used by execute())."""
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
