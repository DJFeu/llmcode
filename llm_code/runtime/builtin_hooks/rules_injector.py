"""Rule-file injection hook (ported from oh-my-opencode/rules-injector)."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from llm_code.runtime.hooks import HookOutcome

if TYPE_CHECKING:
    from llm_code.runtime.hooks import HookRunner

READ_TOOLS: frozenset[str] = frozenset({"read_file", "Read", "ReadFile"})
ROOT_MARKERS: tuple[str, ...] = (
    "pyproject.toml",
    "package.json",
    "go.mod",
    "Cargo.toml",
    ".git",
)
RULE_FILE_NAMES: tuple[str, ...] = (
    "CLAUDE.md",
    "AGENTS.md",
    ".cursorrules",
    ".windsurfrules",
)
MAX_INJECT_BYTES = 16 * 1024

_INJECTED: dict[str, set[str]] = {}


def _find_project_root(file_path: Path) -> Path | None:
    try:
        cur = file_path.resolve().parent
    except OSError:
        return None
    while True:
        for marker in ROOT_MARKERS:
            if (cur / marker).exists():
                return cur
        if cur.parent == cur:
            return None
        cur = cur.parent


def _collect_rule_files(file_path: Path, root: Path) -> list[Path]:
    found: list[Path] = []
    seen: set[Path] = set()
    try:
        cur = file_path.resolve().parent
        root = root.resolve()
    except OSError:
        return found
    while True:
        for name in RULE_FILE_NAMES:
            candidate = cur / name
            if candidate.exists() and candidate not in seen:
                seen.add(candidate)
                found.append(candidate)
        if cur == root or cur.parent == cur:
            break
        cur = cur.parent
    return found


def handle(event: str, context: dict) -> HookOutcome | None:
    if event == "session_end":
        sid = context.get("session_id", "")
        if sid:
            _INJECTED.pop(sid, None)
        return None

    tool = context.get("tool_name", "")
    if tool not in READ_TOOLS:
        return None
    raw_path = context.get("file_path", "") or context.get("path", "")
    if not raw_path:
        return None
    file_path = Path(raw_path)
    if not file_path.exists():
        return None

    root = _find_project_root(file_path)
    if root is None:
        return None

    sid = context.get("session_id", "") or "_default"
    already = _INJECTED.setdefault(sid, set())

    pieces: list[str] = []
    total_bytes = 0
    for rule_file in _collect_rule_files(file_path, root):
        key = str(rule_file)
        if key in already:
            continue
        try:
            body = rule_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        already.add(key)
        header = f"\n\n[Rule file: {rule_file}]\n"
        chunk = header + body
        encoded_len = len(chunk.encode("utf-8"))
        remaining = MAX_INJECT_BYTES - total_bytes
        if encoded_len > remaining:
            allowed = remaining - len(header.encode("utf-8")) - len(b"\n[truncated]")
            if allowed <= 0:
                break
            chunk = (
                header
                + body.encode("utf-8")[:allowed].decode("utf-8", errors="ignore")
                + "\n[truncated]"
            )
            pieces.append(chunk)
            total_bytes += len(chunk.encode("utf-8"))
            break
        pieces.append(chunk)
        total_bytes += encoded_len

    if not pieces:
        return None

    return HookOutcome(
        extra_output="".join(pieces),
        messages=[f"rules_injector: injected {len(pieces)} rule file(s)"],
    )


def register(hook_runner: "HookRunner") -> None:
    hook_runner.subscribe("post_tool_use", handle)
    hook_runner.subscribe("session_end", handle)
