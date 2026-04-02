"""Git-aware tools for interacting with a local git repository."""
from __future__ import annotations

import fnmatch
import os
import subprocess

from pydantic import BaseModel

from llm_code.tools.base import PermissionLevel, Tool, ToolResult

# ---------------------------------------------------------------------------
# Sensitive file patterns — block these in GitCommitTool
# ---------------------------------------------------------------------------

_SENSITIVE_PATTERNS: list[str] = [
    ".env",
    ".env.*",
    "*.key",
    "*.pem",
    "*.p12",
    "credentials.*",
    "secret*",
    "*_secret*",
    "*.credential",
    "token.json",
]


def _is_sensitive(filename: str) -> bool:
    """Return True if filename matches any sensitive pattern."""
    basename = os.path.basename(filename)
    return any(fnmatch.fnmatch(basename, pattern) for pattern in _SENSITIVE_PATTERNS)


def _run_git(args: list[str], cwd: str | None = None) -> ToolResult:
    """Run a git command and return a ToolResult."""
    if cwd is None:
        cwd = os.getcwd()
    result = subprocess.run(
        ["git"] + args,
        capture_output=True,
        text=True,
        cwd=cwd,
    )
    output = result.stdout
    if result.returncode != 0:
        # Combine stderr into output for diagnostics
        output = (result.stderr or result.stdout).strip()
        return ToolResult(output=output, is_error=True)
    return ToolResult(output=output.rstrip("\n"), is_error=False)


# ---------------------------------------------------------------------------
# Input models
# ---------------------------------------------------------------------------


class GitDiffInput(BaseModel):
    path: str = ""
    staged: bool = False
    commit: str = ""


class GitLogInput(BaseModel):
    limit: int = 10
    oneline: bool = True
    path: str = ""


class GitCommitInput(BaseModel):
    message: str
    files: list[str] = []


class GitPushInput(BaseModel):
    remote: str = "origin"
    branch: str = ""


class GitStashInput(BaseModel):
    action: str  # push / pop / list
    message: str = ""


class GitBranchInput(BaseModel):
    action: str  # list / create / switch / delete
    name: str = ""


# ---------------------------------------------------------------------------
# 1. GitStatusTool
# ---------------------------------------------------------------------------


class GitStatusTool(Tool):
    @property
    def name(self) -> str:
        return "git_status"

    @property
    def description(self) -> str:
        return "Show the working-tree status in short format."

    @property
    def input_schema(self) -> dict:
        return {"type": "object", "properties": {}, "required": []}

    @property
    def required_permission(self) -> PermissionLevel:
        return PermissionLevel.READ_ONLY

    def is_read_only(self, args: dict) -> bool:
        return True

    def is_concurrency_safe(self, args: dict) -> bool:
        return True

    def execute(self, args: dict) -> ToolResult:
        return _run_git(["status", "--short"])


# ---------------------------------------------------------------------------
# 2. GitDiffTool
# ---------------------------------------------------------------------------


class GitDiffTool(Tool):
    @property
    def name(self) -> str:
        return "git_diff"

    @property
    def description(self) -> str:
        return "Show changes between commits, working tree, or index."

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "default": ""},
                "staged": {"type": "boolean", "default": False},
                "commit": {"type": "string", "default": ""},
            },
            "required": [],
        }

    @property
    def required_permission(self) -> PermissionLevel:
        return PermissionLevel.READ_ONLY

    @property
    def input_model(self) -> type[GitDiffInput]:
        return GitDiffInput

    def is_read_only(self, args: dict) -> bool:
        return True

    def is_concurrency_safe(self, args: dict) -> bool:
        return True

    def execute(self, args: dict) -> ToolResult:
        cmd: list[str] = ["diff"]
        staged: bool = args.get("staged", False)
        commit: str = args.get("commit", "")
        path: str = args.get("path", "")

        if staged:
            cmd.append("--staged")
        if commit:
            cmd.append(commit)
        if path:
            cmd += ["--", path]

        return _run_git(cmd)


# ---------------------------------------------------------------------------
# 3. GitLogTool
# ---------------------------------------------------------------------------


class GitLogTool(Tool):
    @property
    def name(self) -> str:
        return "git_log"

    @property
    def description(self) -> str:
        return "Show the commit log."

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 10},
                "oneline": {"type": "boolean", "default": True},
                "path": {"type": "string", "default": ""},
            },
            "required": [],
        }

    @property
    def required_permission(self) -> PermissionLevel:
        return PermissionLevel.READ_ONLY

    @property
    def input_model(self) -> type[GitLogInput]:
        return GitLogInput

    def is_read_only(self, args: dict) -> bool:
        return True

    def is_concurrency_safe(self, args: dict) -> bool:
        return True

    def execute(self, args: dict) -> ToolResult:
        limit: int = int(args.get("limit", 10))
        oneline: bool = args.get("oneline", True)
        path: str = args.get("path", "")

        cmd: list[str] = ["log", f"-n{limit}"]
        if oneline:
            cmd.append("--oneline")
        if path:
            cmd += ["--", path]

        return _run_git(cmd)


# ---------------------------------------------------------------------------
# 4. GitCommitTool
# ---------------------------------------------------------------------------


class GitCommitTool(Tool):
    @property
    def name(self) -> str:
        return "git_commit"

    @property
    def description(self) -> str:
        return "Stage files and create a git commit."

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "message": {"type": "string"},
                "files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "default": [],
                },
            },
            "required": ["message"],
        }

    @property
    def required_permission(self) -> PermissionLevel:
        return PermissionLevel.WORKSPACE_WRITE

    @property
    def input_model(self) -> type[GitCommitInput]:
        return GitCommitInput

    def is_read_only(self, args: dict) -> bool:
        return False

    def is_destructive(self, args: dict) -> bool:
        return False

    def execute(self, args: dict) -> ToolResult:
        message: str = args["message"]
        files: list[str] = args.get("files", [])

        # Safety: reject sensitive files
        sensitive = [f for f in files if _is_sensitive(f)]
        if sensitive:
            return ToolResult(
                output=f"Blocked: sensitive file(s) detected: {', '.join(sensitive)}",
                is_error=True,
            )

        cwd = os.getcwd()

        # Stage files
        if files:
            add_result = subprocess.run(
                ["git", "add"] + files,
                capture_output=True,
                text=True,
                cwd=cwd,
            )
        else:
            add_result = subprocess.run(
                ["git", "add", "-A"],
                capture_output=True,
                text=True,
                cwd=cwd,
            )

        if add_result.returncode != 0:
            return ToolResult(
                output=(add_result.stderr or add_result.stdout).strip(),
                is_error=True,
            )

        commit_result = subprocess.run(
            ["git", "commit", "-m", message],
            capture_output=True,
            text=True,
            cwd=cwd,
        )
        if commit_result.returncode != 0:
            return ToolResult(
                output=(commit_result.stderr or commit_result.stdout).strip(),
                is_error=True,
            )

        return ToolResult(output=commit_result.stdout.rstrip("\n"), is_error=False)


# ---------------------------------------------------------------------------
# 5. GitPushTool
# ---------------------------------------------------------------------------


class GitPushTool(Tool):
    @property
    def name(self) -> str:
        return "git_push"

    @property
    def description(self) -> str:
        return "Push commits to a remote repository."

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "remote": {"type": "string", "default": "origin"},
                "branch": {"type": "string", "default": ""},
            },
            "required": [],
        }

    @property
    def required_permission(self) -> PermissionLevel:
        return PermissionLevel.FULL_ACCESS

    @property
    def input_model(self) -> type[GitPushInput]:
        return GitPushInput

    def is_read_only(self, args: dict) -> bool:
        return False

    def is_destructive(self, args: dict) -> bool:
        return True

    def execute(self, args: dict) -> ToolResult:
        remote: str = args.get("remote", "origin")
        branch: str = args.get("branch", "")

        cmd: list[str] = ["push", remote]
        if branch:
            cmd.append(branch)

        return _run_git(cmd)


# ---------------------------------------------------------------------------
# 6. GitStashTool
# ---------------------------------------------------------------------------


class GitStashTool(Tool):
    @property
    def name(self) -> str:
        return "git_stash"

    @property
    def description(self) -> str:
        return "Stash or restore changes in the working directory."

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["push", "pop", "list"]},
                "message": {"type": "string", "default": ""},
            },
            "required": ["action"],
        }

    @property
    def required_permission(self) -> PermissionLevel:
        return PermissionLevel.WORKSPACE_WRITE

    @property
    def input_model(self) -> type[GitStashInput]:
        return GitStashInput

    def execute(self, args: dict) -> ToolResult:
        action: str = args["action"]
        message: str = args.get("message", "")

        if action == "push":
            cmd = ["stash", "push"]
            if message:
                cmd += ["-m", message]
        elif action == "pop":
            cmd = ["stash", "pop"]
        elif action == "list":
            cmd = ["stash", "list"]
        else:
            return ToolResult(
                output=f"Unknown stash action: {action!r}. Use push, pop, or list.",
                is_error=True,
            )

        return _run_git(cmd)


# ---------------------------------------------------------------------------
# 7. GitBranchTool
# ---------------------------------------------------------------------------


class GitBranchTool(Tool):
    @property
    def name(self) -> str:
        return "git_branch"

    @property
    def description(self) -> str:
        return "List, create, switch, or delete git branches."

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["list", "create", "switch", "delete"]},
                "name": {"type": "string", "default": ""},
            },
            "required": ["action"],
        }

    @property
    def required_permission(self) -> PermissionLevel:
        return PermissionLevel.WORKSPACE_WRITE

    @property
    def input_model(self) -> type[GitBranchInput]:
        return GitBranchInput

    def is_destructive(self, args: dict) -> bool:
        return args.get("action") == "delete"

    def execute(self, args: dict) -> ToolResult:
        action: str = args["action"]
        name: str = args.get("name", "")

        if action == "list":
            cmd = ["branch", "-a"]
        elif action == "create":
            if not name:
                return ToolResult(output="Branch name required for create.", is_error=True)
            cmd = ["checkout", "-b", name]
        elif action == "switch":
            if not name:
                return ToolResult(output="Branch name required for switch.", is_error=True)
            cmd = ["checkout", name]
        elif action == "delete":
            if not name:
                return ToolResult(output="Branch name required for delete.", is_error=True)
            cmd = ["branch", "-d", name]
        else:
            return ToolResult(
                output=f"Unknown branch action: {action!r}. Use list, create, switch, or delete.",
                is_error=True,
            )

        return _run_git(cmd)
