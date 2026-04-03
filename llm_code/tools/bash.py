"""BashTool — execute shell commands with timeout and safety checks."""
from __future__ import annotations

import re
import select
import subprocess
from dataclasses import dataclass, field
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
# Safety result dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BashSafetyResult:
    """Classification result from the bash safety checker.

    classification:
        "safe"          — proceed without confirmation
        "needs_confirm" — ask user before executing
        "blocked"       — refuse execution outright
    """

    classification: str  # "safe" | "needs_confirm" | "blocked"
    reasons: tuple[str, ...] = field(default_factory=tuple)
    rule_ids: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_safe(self) -> bool:
        return self.classification == "safe"

    @property
    def is_blocked(self) -> bool:
        return self.classification == "blocked"

    @property
    def needs_confirm(self) -> bool:
        return self.classification == "needs_confirm"


# ---------------------------------------------------------------------------
# Pattern lists — original 7 checks
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

# Destructive patterns (require confirmation)
_DESTRUCTIVE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\brm\s+(-[^\s]*r|-rf?)\b", re.IGNORECASE),
    re.compile(r"\bgit\s+(push|reset|rebase|merge|clean)\b", re.IGNORECASE),
    re.compile(r"\bdrop\s+table\b", re.IGNORECASE),
    re.compile(r"\btruncate\b", re.IGNORECASE),
    re.compile(r"\bmkfs\b", re.IGNORECASE),
    re.compile(r"\bdd\s+if=", re.IGNORECASE),
]


# ---------------------------------------------------------------------------
# New security check patterns — rules 8–20
# ---------------------------------------------------------------------------

# Rule 8: Command injection — $(…), backticks, ${…} nested in args
_CMD_INJECTION_PATTERN = re.compile(r"\$\(|`[^`]+`|\$\{[^}]+\}")

# Rule 9: Newline attack — literal \n or \r hiding commands
_NEWLINE_ATTACK_PATTERN = re.compile(r"(\\n|\\r|\x0a|\x0d)")

# Rule 10: Pipe chain — >5 pipes
_PIPE_CHAIN_PATTERN = re.compile(r"(?:\|(?!\|)){6,}")  # 6 or more | (not ||)

# Rule 11: Interpreter REPL — interactive interpreter with no filename argument
# Matches: python, python3, node, ruby, perl, php with no trailing filename/flags
_REPL_PATTERN = re.compile(
    r"^\s*(python3?|node|ruby|perl|php)\s*$",
    re.IGNORECASE,
)

# Rule 12: Env leak — env/printenv/export commands may expose secrets
_ENV_LEAK_PATTERN = re.compile(r"^\s*(env|printenv|export)\b", re.IGNORECASE)

# Rule 13: Network access to non-localhost
# curl/wget/nc/ssh pointing to non-localhost destinations
_NETWORK_LOCALHOST_PATTERN = re.compile(
    r"\b(curl|wget|nc|ssh)\s+[^\s]*?(localhost|127\.0\.0\.1|::1)",
    re.IGNORECASE,
)
_NETWORK_ACCESS_PATTERN = re.compile(r"\b(curl|wget|nc|ssh)\b", re.IGNORECASE)

# Rule 14: File permission changes
_FILE_PERMISSION_PATTERN = re.compile(r"\b(chmod|chown|chgrp)\b", re.IGNORECASE)

# Rule 15: System package installation
_SYSTEM_PACKAGE_PATTERN = re.compile(
    r"\b(apt(?:-get)?|brew)\s+(install|upgrade|update)\b"
    r"|\bpip\s+install\b"
    r"|\bnpm\s+install\s+-g\b",
    re.IGNORECASE,
)

# Rule 16: Redirect overwrite (> but not >>)
_REDIRECT_OVERWRITE_PATTERN = re.compile(r"(?<!>)>(?!>)")

# Rule 17: Credential file access
_CREDENTIAL_ACCESS_PATTERN = re.compile(
    r"(~\/\.ssh|~\/\.aws|~\/\.config|\.env\b|/\.ssh/|/\.aws/|/\.config/)",
    re.IGNORECASE,
)

# Rule 18: Background execution
_BACKGROUND_EXEC_PATTERN = re.compile(r"\s&\s*$|\s&\s+|\bnohup\b|\bdisown\b")

# Rule 19: Recursive ops with find -exec or xargs + write commands
_RECURSIVE_OPS_PATTERN = re.compile(
    r"\bfind\b.*-exec\b|\bxargs\b.*(rm|mv|cp|chmod|chown|dd|truncate|tee|write)\b",
    re.IGNORECASE,
)

# Rule 20: Multi-command chaining >3 commands (&&, ||, ;)
_MULTI_CMD_SEPARATOR = re.compile(r"(&&|\|\||;)")


def _count_pipe_segments(command: str) -> int:
    """Count number of pipe characters (not ||) in command."""
    # Remove || first, then count |
    cleaned = command.replace("||", "\x00\x00")
    return cleaned.count("|")


def _count_command_chain(command: str) -> int:
    """Count commands separated by &&, ||, or ; (not inside quotes, approximate)."""
    return len(_MULTI_CMD_SEPARATOR.findall(command))


def _is_network_to_non_localhost(command: str) -> bool:
    """Return True if command uses a network tool pointing to non-localhost."""
    if not _NETWORK_ACCESS_PATTERN.search(command):
        return False
    # If it explicitly targets localhost, it's fine
    if _NETWORK_LOCALHOST_PATTERN.search(command):
        return False
    return True


# ---------------------------------------------------------------------------
# Internal helpers — original checks
# ---------------------------------------------------------------------------


def _is_truly_dangerous(command: str) -> bool:
    return any(p.search(command) for p in _TRULY_DANGEROUS_PATTERNS)


# ---------------------------------------------------------------------------
# Core safety classifier
# ---------------------------------------------------------------------------


def classify_command(command: str) -> BashSafetyResult:
    """Classify a bash command and return a BashSafetyResult.

    Rules 1–7  map to the original pattern lists.
    Rules 8–20 are the new extended security checks.
    """
    reasons: list[str] = []
    rule_ids: list[str] = []
    classification = "safe"

    # --- Blocked (rules 1–7 truly dangerous) --------------------------------
    if _is_truly_dangerous(command):
        return BashSafetyResult(
            classification="blocked",
            reasons=("Truly dangerous command detected",),
            rule_ids=("R1-R7",),
        )

    # --- Rule 8: Command injection ------------------------------------------
    if _CMD_INJECTION_PATTERN.search(command):
        reasons.append("Command injection pattern detected: $(...), backtick, or ${...}")
        rule_ids.append("R8")
        classification = "needs_confirm"

    # --- Rule 9: Newline attack ----------------------------------------------
    if _NEWLINE_ATTACK_PATTERN.search(command):
        reasons.append("Newline/carriage-return character may hide injected commands")
        rule_ids.append("R9")
        classification = "needs_confirm"

    # --- Rule 10: Pipe chain > 5 pipes ---------------------------------------
    pipe_count = _count_pipe_segments(command)
    if pipe_count > 5:
        reasons.append(f"Long pipe chain ({pipe_count} pipes) needs confirmation")
        rule_ids.append("R10")
        classification = "needs_confirm"

    # --- Rule 11: Interpreter REPL (auto mode blocks interactive) ------------
    if _REPL_PATTERN.search(command):
        reasons.append("Interactive interpreter REPL blocked in auto mode (no filename given)")
        rule_ids.append("R11")
        # Upgrade to blocked for REPL
        classification = "blocked"

    # --- Rule 12: Env leak ---------------------------------------------------
    if _ENV_LEAK_PATTERN.search(command):
        reasons.append("env/printenv/export may expose secrets")
        rule_ids.append("R12")
        if classification == "safe":
            classification = "needs_confirm"

    # --- Rule 13: Network access to non-localhost ----------------------------
    if _is_network_to_non_localhost(command):
        reasons.append("Network access to non-localhost host needs confirmation")
        rule_ids.append("R13")
        if classification != "blocked":
            classification = "needs_confirm"

    # --- Rule 14: File permissions -------------------------------------------
    if _FILE_PERMISSION_PATTERN.search(command):
        reasons.append("chmod/chown/chgrp modifies file permissions")
        rule_ids.append("R14")
        if classification != "blocked":
            classification = "needs_confirm"

    # --- Rule 15: System package installation --------------------------------
    if _SYSTEM_PACKAGE_PATTERN.search(command):
        reasons.append("System package installation needs confirmation")
        rule_ids.append("R15")
        if classification != "blocked":
            classification = "needs_confirm"

    # --- Rule 16: Redirect overwrite -----------------------------------------
    if _REDIRECT_OVERWRITE_PATTERN.search(command):
        reasons.append("Output redirect (>) may overwrite existing file")
        rule_ids.append("R16")
        if classification != "blocked":
            classification = "needs_confirm"

    # --- Rule 17: Credential file access -------------------------------------
    if _CREDENTIAL_ACCESS_PATTERN.search(command):
        reasons.append("Command accesses credential or config files (~/.ssh, ~/.aws, .env…)")
        rule_ids.append("R17")
        if classification != "blocked":
            classification = "needs_confirm"

    # --- Rule 18: Background execution ---------------------------------------
    if _BACKGROUND_EXEC_PATTERN.search(command):
        reasons.append("Background execution (&, nohup, disown) needs confirmation")
        rule_ids.append("R18")
        if classification != "blocked":
            classification = "needs_confirm"

    # --- Rule 19: Recursive ops with find -exec / xargs + writes -------------
    if _RECURSIVE_OPS_PATTERN.search(command):
        reasons.append("Recursive operation with find -exec or xargs+write needs confirmation")
        rule_ids.append("R19")
        if classification != "blocked":
            classification = "needs_confirm"

    # --- Rule 20: Multi-command chaining > 3 commands ------------------------
    chain_count = _count_command_chain(command)
    if chain_count >= 3:
        reasons.append(f"Multi-command chain ({chain_count + 1} commands) needs confirmation")
        rule_ids.append("R20")
        if classification != "blocked":
            classification = "needs_confirm"

    return BashSafetyResult(
        classification=classification,
        reasons=tuple(reasons),
        rule_ids=tuple(rule_ids),
    )


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

    def classify(self, args: dict) -> BashSafetyResult:
        """Return a BashSafetyResult for the command in *args*."""
        command: str = args.get("command", "")
        return classify_command(command)

    def is_read_only(self, args: dict) -> bool:
        command: str = args.get("command", "")
        # A command is read-only only if it matches a read-only pattern AND
        # the safety classifier does not flag it as needs_confirm or blocked
        # (rules 8–20 override the read-only optimistic label).
        if not any(p.search(command) for p in _READ_ONLY_PATTERNS):
            return False
        result = classify_command(command)
        # If the classifier found something suspicious, it is not purely read-only
        return result.is_safe

    def is_destructive(self, args: dict) -> bool:
        command: str = args.get("command", "")
        if any(p.search(command) for p in _DESTRUCTIVE_PATTERNS):
            return True
        # Also treat "needs_confirm" from the extended rules as destructive
        result = classify_command(command)
        return result.needs_confirm or result.is_blocked

    def is_concurrency_safe(self, args: dict) -> bool:
        return self.is_read_only(args)

    def execute(self, args: dict) -> ToolResult:
        command: str = args["command"]
        timeout: int = int(args.get("timeout", self._default_timeout))

        result = classify_command(command)
        if result.is_blocked:
            return ToolResult(
                output=f"Dangerous command blocked: {command}\nReasons: {'; '.join(result.reasons)}",
                is_error=True,
                metadata={"dangerous": True, "rule_ids": list(result.rule_ids)},
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
