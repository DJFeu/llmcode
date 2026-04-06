"""Sanitize untrusted content before injection into system prompt."""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

_MAX_INSTRUCTION_LENGTH = 4096

_INSTRUCTION_BLOCK_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("override_safety", re.compile(
        r"ignore\s+(all\s+)?(rules|safety|restrictions|instructions|guidelines)",
        re.IGNORECASE,
    )),
    ("role_hijack", re.compile(
        r"you\s+are\s+now|from\s+now\s+on\s+you|act\s+as\s+if|pretend\s+(to\s+be|you)",
        re.IGNORECASE,
    )),
    ("secret_exfil", re.compile(
        r"(read|cat|output|send|show|print).{0,30}(ssh|api.?key|secret|credential|token|password|\.env)",
        re.IGNORECASE,
    )),
    ("tool_override", re.compile(
        r"(execute|run|call)\s+(this\s+)?(command|tool|function)\s*(before|instead|after)",
        re.IGNORECASE,
    )),
)


def sanitize_mcp_instructions(
    server_name: str, instructions: str,
) -> tuple[str, list[str]]:
    """Sanitize MCP server instructions.

    Returns (cleaned_text, warnings). Warnings list is empty if clean.
    - Truncates overly long instructions
    - Strips lines matching injection patterns
    """
    warnings: list[str] = []

    if len(instructions) > _MAX_INSTRUCTION_LENGTH:
        warnings.append(
            f"MCP '{server_name}': instructions truncated "
            f"({len(instructions)} -> {_MAX_INSTRUCTION_LENGTH} chars)"
        )
        instructions = instructions[:_MAX_INSTRUCTION_LENGTH]

    cleaned_lines: list[str] = []
    for line in instructions.splitlines():
        blocked = False
        for rule_id, pattern in _INSTRUCTION_BLOCK_PATTERNS:
            if pattern.search(line):
                warnings.append(
                    f"MCP '{server_name}': blocked instruction "
                    f"(rule: {rule_id}): {line[:80]}"
                )
                blocked = True
                break
        if not blocked:
            cleaned_lines.append(line)

    return "\n".join(cleaned_lines), warnings
