"""Tests for BashTool v2 — safety classification, Pydantic validation, progress streaming."""
from __future__ import annotations

from typing import List

import pytest
from pydantic import ValidationError

from llm_code.tools.base import ToolProgress, ToolResult
from llm_code.tools.bash import BashInput, BashTool


@pytest.fixture()
def tool() -> BashTool:
    return BashTool()


# ---------------------------------------------------------------------------
# BashInput validation
# ---------------------------------------------------------------------------


class TestBashInput:
    def test_valid_command(self) -> None:
        inp = BashInput(command="ls -la")
        assert inp.command == "ls -la"
        assert inp.timeout == 30  # default

    def test_custom_timeout(self) -> None:
        inp = BashInput(command="echo hi", timeout=60)
        assert inp.timeout == 60

    def test_missing_command_raises(self) -> None:
        with pytest.raises(ValidationError):
            BashInput()  # type: ignore[call-arg]

    def test_empty_command_allowed(self) -> None:
        # Empty string is a valid str; actual execution is bash's problem
        inp = BashInput(command="")
        assert inp.command == ""

    def test_timeout_default_is_30(self) -> None:
        inp = BashInput(command="pwd")
        assert inp.timeout == 30


# ---------------------------------------------------------------------------
# is_read_only
# ---------------------------------------------------------------------------


class TestIsReadOnly:
    @pytest.mark.parametrize(
        "command",
        [
            "ls",
            "ls -la /tmp",
            "cat /etc/hosts",
            "head -n 10 file.txt",
            "tail -f /var/log/syslog",
            "wc -l file.py",
            "echo hello",
            "pwd",
            "whoami",
            "date",
            "uname -a",
            "which python",
            "type bash",
            "file /usr/bin/ls",
            "stat /tmp",
            "grep foo bar.txt",
            "rg pattern src/",
            "find . -name '*.py'",
            "fd '*.py'",
            "git status",
            "git log --oneline",
            "git diff HEAD",
            "git show HEAD",
            "git branch",
            "git remote -v",
            "git tag",
            "env",
            "printenv PATH",
            "id",
            "hostname",
            "df -h",
            "du -sh /tmp",
            "ps aux",
        ],
    )
    def test_is_read_only_true(self, tool: BashTool, command: str) -> None:
        assert tool.is_read_only({"command": command}) is True, f"Expected read-only: {command!r}"

    @pytest.mark.parametrize(
        "command",
        [
            "npm install",
            "pip install requests",
            "mkdir /tmp/newdir",
            "touch file.txt",
            "cp src dst",
            "mv src dst",
            "chmod 755 script.sh",
            "git commit -m 'msg'",
            "git push origin main",
            "python script.py",
            "node server.js",
            "make build",
            "rm file.txt",
        ],
    )
    def test_is_read_only_false(self, tool: BashTool, command: str) -> None:
        assert tool.is_read_only({"command": command}) is False, f"Expected NOT read-only: {command!r}"


# ---------------------------------------------------------------------------
# is_destructive
# ---------------------------------------------------------------------------


class TestIsDestructive:
    @pytest.mark.parametrize(
        "command",
        [
            "rm -rf /tmp/foo",
            "rm -r /home/user",
            "git push origin main",
            "git push --force",
            "git reset --hard HEAD",
            "git reset HEAD~1",
            "git rebase main",
            "git merge feature",
            "git clean -fd",
            "DROP TABLE users",
            "drop table orders;",
            "TRUNCATE TABLE logs",
            "truncate logs;",
            "mkfs.ext4 /dev/sda",
            "dd if=/dev/zero of=/dev/sda",
        ],
    )
    def test_is_destructive_true(self, tool: BashTool, command: str) -> None:
        assert tool.is_destructive({"command": command}) is True, f"Expected destructive: {command!r}"

    @pytest.mark.parametrize(
        "command",
        [
            "ls",
            "cat file.txt",
            "npm install",
            "pip install requests",
            "git status",
            "git log",
            "git diff",
            "echo hello",
            "pwd",
        ],
    )
    def test_is_destructive_false(self, tool: BashTool, command: str) -> None:
        assert tool.is_destructive({"command": command}) is False, f"Expected NOT destructive: {command!r}"


# ---------------------------------------------------------------------------
# is_concurrency_safe
# ---------------------------------------------------------------------------


class TestIsConcurrencySafe:
    @pytest.mark.parametrize(
        "command",
        [
            "ls",
            "cat file.txt",
            "grep foo bar",
            "git status",
            "git log",
        ],
    )
    def test_is_concurrency_safe_true_for_read_only(self, tool: BashTool, command: str) -> None:
        assert tool.is_concurrency_safe({"command": command}) is True

    @pytest.mark.parametrize(
        "command",
        [
            "npm install",
            "pip install requests",
            "make build",
            "python script.py",
        ],
    )
    def test_is_concurrency_safe_false_for_non_read_only(self, tool: BashTool, command: str) -> None:
        assert tool.is_concurrency_safe({"command": command}) is False


# ---------------------------------------------------------------------------
# execute_with_progress
# ---------------------------------------------------------------------------


class TestExecuteWithProgress:
    def test_short_command_works(self, tool: BashTool) -> None:
        events: List[ToolProgress] = []
        result = tool.execute_with_progress(
            {"command": "echo hello"},
            on_progress=events.append,
        )
        assert isinstance(result, ToolResult)
        assert "hello" in result.output
        assert not result.is_error

    def test_long_command_emits_progress_events(self, tool: BashTool) -> None:
        events: List[ToolProgress] = []
        # Command that runs for ~2 seconds, printing lines periodically
        result = tool.execute_with_progress(
            {
                "command": (
                    "for i in 1 2 3 4; do echo line$i; sleep 0.4; done"
                ),
                "timeout": 10,
            },
            on_progress=events.append,
        )
        assert not result.is_error
        assert "line4" in result.output
        # Should have emitted at least one progress event during the run
        assert len(events) >= 1
        # All events should be ToolProgress instances
        for ev in events:
            assert isinstance(ev, ToolProgress)
            assert ev.tool_name == "bash"

    def test_timeout_is_handled(self, tool: BashTool) -> None:
        events: List[ToolProgress] = []
        result = tool.execute_with_progress(
            {"command": "sleep 60", "timeout": 1},
            on_progress=events.append,
        )
        assert result.is_error
        assert "timed out" in result.output.lower() or "timeout" in result.output.lower()

    def test_progress_event_has_message(self, tool: BashTool) -> None:
        events: List[ToolProgress] = []
        tool.execute_with_progress(
            {"command": "for i in 1 2 3; do echo item$i; sleep 0.3; done", "timeout": 10},
            on_progress=events.append,
        )
        if events:
            assert events[0].message  # should be non-empty string

    def test_returns_tool_result(self, tool: BashTool) -> None:
        result = tool.execute_with_progress({"command": "pwd"}, on_progress=lambda _: None)
        assert isinstance(result, ToolResult)

    def test_input_model_property(self, tool: BashTool) -> None:
        assert tool.input_model is BashInput

    def test_validate_input_coerces_correctly(self, tool: BashTool) -> None:
        validated = tool.validate_input({"command": "ls", "timeout": "5"})
        assert validated["command"] == "ls"
        assert validated["timeout"] == 5

    def test_validate_input_missing_command_raises(self, tool: BashTool) -> None:
        with pytest.raises(ValidationError):
            tool.validate_input({})
