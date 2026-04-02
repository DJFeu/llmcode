"""Tests for the hook runner system."""
from __future__ import annotations

import os
import stat

import pytest

from llm_code.tools.base import ToolResult
from llm_code.runtime.config import HookConfig
from llm_code.runtime.hooks import HookOutcome, HookRunner


class TestHookOutcome:
    def test_defaults(self):
        outcome = HookOutcome()
        assert outcome.denied is False
        assert outcome.messages == []

    def test_denied(self):
        outcome = HookOutcome(denied=True, messages=["blocked"])
        assert outcome.denied is True
        assert "blocked" in outcome.messages


class TestNoHooks:
    def test_pre_tool_use_no_hooks(self):
        runner = HookRunner()
        outcome = runner.pre_tool_use("read_file", {"path": "/tmp/test"})
        assert outcome.denied is False
        assert outcome.messages == []

    def test_post_tool_use_no_hooks(self):
        runner = HookRunner()
        result = ToolResult(output="ok")
        outcome = runner.post_tool_use("read_file", {"path": "/tmp/test"}, result)
        assert outcome.denied is False
        assert outcome.messages == []


class TestMatchingHook:
    def test_pre_tool_use_matching_hook_runs(self, tmp_path):
        script = tmp_path / "pre_hook.sh"
        script.write_text("#!/bin/sh\nexit 0\n")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)

        hook = HookConfig(event="pre_tool_use", command=str(script))
        runner = HookRunner(hooks=(hook,))
        outcome = runner.pre_tool_use("any_tool", {})
        assert outcome.denied is False

    def test_pre_tool_use_tool_pattern_matches(self, tmp_path):
        script = tmp_path / "check.sh"
        script.write_text("#!/bin/sh\nexit 0\n")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)

        hook = HookConfig(event="pre_tool_use", command=str(script), tool_pattern="bash_*")
        runner = HookRunner(hooks=(hook,))
        outcome = runner.pre_tool_use("bash_exec", {})
        assert outcome.denied is False

    def test_non_matching_pattern_skipped(self, tmp_path):
        script = tmp_path / "hook.sh"
        # Script exits 2 (deny), but pattern won't match
        script.write_text("#!/bin/sh\nexit 2\n")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)

        hook = HookConfig(event="pre_tool_use", command=str(script), tool_pattern="bash_*")
        runner = HookRunner(hooks=(hook,))
        # Tool name doesn't match "bash_*", so hook is skipped, no deny
        outcome = runner.pre_tool_use("read_file", {})
        assert outcome.denied is False

    def test_wrong_event_skipped(self, tmp_path):
        script = tmp_path / "hook.sh"
        script.write_text("#!/bin/sh\nexit 2\n")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)

        hook = HookConfig(event="post_tool_use", command=str(script))
        runner = HookRunner(hooks=(hook,))
        # event mismatch: hook is post_tool_use but we call pre_tool_use
        outcome = runner.pre_tool_use("bash", {})
        assert outcome.denied is False


class TestDenyOnExit2:
    def test_pre_tool_use_exit_2_denies(self, tmp_path):
        script = tmp_path / "deny_hook.sh"
        script.write_text("#!/bin/sh\nexit 2\n")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)

        hook = HookConfig(event="pre_tool_use", command=str(script))
        runner = HookRunner(hooks=(hook,))
        outcome = runner.pre_tool_use("bash", {})
        assert outcome.denied is True

    def test_post_tool_use_exit_2_denies(self, tmp_path):
        script = tmp_path / "deny_hook.sh"
        script.write_text("#!/bin/sh\nexit 2\n")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)

        hook = HookConfig(event="post_tool_use", command=str(script))
        runner = HookRunner(hooks=(hook,))
        result = ToolResult(output="some output")
        outcome = runner.post_tool_use("bash", {}, result)
        assert outcome.denied is True


class TestWarnOnOtherExit:
    def test_exit_1_produces_warning_not_deny(self, tmp_path):
        script = tmp_path / "warn_hook.sh"
        script.write_text("#!/bin/sh\nexit 1\n")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)

        hook = HookConfig(event="pre_tool_use", command=str(script))
        runner = HookRunner(hooks=(hook,))
        outcome = runner.pre_tool_use("bash", {})
        assert outcome.denied is False
        assert len(outcome.messages) > 0  # should have a warning message

    def test_exit_42_produces_warning_not_deny(self, tmp_path):
        script = tmp_path / "warn_hook.sh"
        script.write_text("#!/bin/sh\nexit 42\n")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)

        hook = HookConfig(event="pre_tool_use", command=str(script))
        runner = HookRunner(hooks=(hook,))
        outcome = runner.pre_tool_use("bash", {})
        assert outcome.denied is False
        assert len(outcome.messages) > 0


class TestEnvVarsPassedToHook:
    def test_hook_event_env_var(self, tmp_path):
        marker = tmp_path / "event.txt"
        script = tmp_path / "env_hook.sh"
        script.write_text(f'#!/bin/sh\necho "$HOOK_EVENT" > {marker}\nexit 0\n')
        script.chmod(script.stat().st_mode | stat.S_IEXEC)

        hook = HookConfig(event="pre_tool_use", command=str(script))
        runner = HookRunner(hooks=(hook,))
        runner.pre_tool_use("bash", {})
        assert marker.read_text().strip() == "pre_tool_use"

    def test_hook_tool_name_env_var(self, tmp_path):
        marker = tmp_path / "tool.txt"
        script = tmp_path / "env_hook.sh"
        script.write_text(f'#!/bin/sh\necho "$HOOK_TOOL_NAME" > {marker}\nexit 0\n')
        script.chmod(script.stat().st_mode | stat.S_IEXEC)

        hook = HookConfig(event="pre_tool_use", command=str(script))
        runner = HookRunner(hooks=(hook,))
        runner.pre_tool_use("read_file", {"path": "/test"})
        assert marker.read_text().strip() == "read_file"

    def test_hook_tool_input_env_var(self, tmp_path):
        marker = tmp_path / "input.txt"
        script = tmp_path / "env_hook.sh"
        script.write_text(f'#!/bin/sh\necho "$HOOK_TOOL_INPUT" > {marker}\nexit 0\n')
        script.chmod(script.stat().st_mode | stat.S_IEXEC)

        hook = HookConfig(event="pre_tool_use", command=str(script))
        runner = HookRunner(hooks=(hook,))
        runner.pre_tool_use("bash", {"cmd": "ls"})
        content = marker.read_text().strip()
        assert "cmd" in content  # JSON encoded args should contain the key

    def test_post_tool_output_env_var(self, tmp_path):
        marker = tmp_path / "output.txt"
        script = tmp_path / "env_hook.sh"
        script.write_text(f'#!/bin/sh\necho "$HOOK_TOOL_OUTPUT" > {marker}\nexit 0\n')
        script.chmod(script.stat().st_mode | stat.S_IEXEC)

        hook = HookConfig(event="post_tool_use", command=str(script))
        runner = HookRunner(hooks=(hook,))
        result = ToolResult(output="hello world")
        runner.post_tool_use("bash", {}, result)
        assert marker.read_text().strip() == "hello world"

    def test_post_tool_is_error_env_var(self, tmp_path):
        marker = tmp_path / "is_error.txt"
        script = tmp_path / "env_hook.sh"
        script.write_text(f'#!/bin/sh\necho "$HOOK_TOOL_IS_ERROR" > {marker}\nexit 0\n')
        script.chmod(script.stat().st_mode | stat.S_IEXEC)

        hook = HookConfig(event="post_tool_use", command=str(script))
        runner = HookRunner(hooks=(hook,))
        result = ToolResult(output="error occurred", is_error=True)
        runner.post_tool_use("bash", {}, result)
        assert marker.read_text().strip() == "true"


class TestMultipleHooks:
    def test_all_matching_hooks_run(self, tmp_path):
        marker1 = tmp_path / "ran1.txt"
        marker2 = tmp_path / "ran2.txt"

        script1 = tmp_path / "hook1.sh"
        script1.write_text(f'#!/bin/sh\ntouch {marker1}\nexit 0\n')
        script1.chmod(script1.stat().st_mode | stat.S_IEXEC)

        script2 = tmp_path / "hook2.sh"
        script2.write_text(f'#!/bin/sh\ntouch {marker2}\nexit 0\n')
        script2.chmod(script2.stat().st_mode | stat.S_IEXEC)

        hooks = (
            HookConfig(event="pre_tool_use", command=str(script1)),
            HookConfig(event="pre_tool_use", command=str(script2)),
        )
        runner = HookRunner(hooks=hooks)
        runner.pre_tool_use("bash", {})
        assert marker1.exists()
        assert marker2.exists()

    def test_first_deny_stops_evaluation(self, tmp_path):
        """If first hook exits 2, second hook should not run."""
        marker2 = tmp_path / "ran2.txt"

        script1 = tmp_path / "deny.sh"
        script1.write_text("#!/bin/sh\nexit 2\n")
        script1.chmod(script1.stat().st_mode | stat.S_IEXEC)

        script2 = tmp_path / "hook2.sh"
        script2.write_text(f'#!/bin/sh\ntouch {marker2}\nexit 0\n')
        script2.chmod(script2.stat().st_mode | stat.S_IEXEC)

        hooks = (
            HookConfig(event="pre_tool_use", command=str(script1)),
            HookConfig(event="pre_tool_use", command=str(script2)),
        )
        runner = HookRunner(hooks=hooks)
        outcome = runner.pre_tool_use("bash", {})
        assert outcome.denied is True
        assert not marker2.exists()
