"""Tests for expanded hook system: 6 types, 24 events, glob matching, on_error, timeout."""
from __future__ import annotations

import stat

import pytest

from llm_code.runtime.config import HookConfig
from llm_code.runtime.hooks import HookRunner


# ---------------------------------------------------------------------------
# HookConfig field defaults
# ---------------------------------------------------------------------------

class TestHookConfigDefaults:
    def test_timeout_default(self):
        h = HookConfig(event="pre_tool_use", command="true")
        assert h.timeout == 10.0

    def test_on_error_default(self):
        h = HookConfig(event="pre_tool_use", command="true")
        assert h.on_error == "warn"

    def test_explicit_timeout(self):
        h = HookConfig(event="session_start", command="true", timeout=30.0)
        assert h.timeout == 30.0

    def test_explicit_on_error_deny(self):
        h = HookConfig(event="http_request", command="true", on_error="deny")
        assert h.on_error == "deny"

    def test_explicit_on_error_ignore(self):
        h = HookConfig(event="agent_spawn", command="true", on_error="ignore")
        assert h.on_error == "ignore"


# ---------------------------------------------------------------------------
# Glob matching on event names
# ---------------------------------------------------------------------------

class TestEventGlobMatching:
    def test_wildcard_matches_all_events(self, tmp_path):
        marker = tmp_path / "ran.txt"
        script = tmp_path / "hook.sh"
        script.write_text(f"#!/bin/sh\ntouch {marker}\nexit 0\n")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)

        hook = HookConfig(event="*", command=str(script))
        runner = HookRunner(hooks=(hook,))
        runner.fire("pre_tool_use", {})
        assert marker.exists()

    def test_tool_glob_matches_tool_events(self, tmp_path):
        marker = tmp_path / "ran.txt"
        script = tmp_path / "hook.sh"
        script.write_text(f"#!/bin/sh\ntouch {marker}\nexit 0\n")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)

        hook = HookConfig(event="tool.*", command=str(script))
        runner = HookRunner(hooks=(hook,))
        runner.fire("tool_error", {})
        assert marker.exists()

    def test_tool_glob_does_not_match_session_events(self, tmp_path):
        marker = tmp_path / "ran.txt"
        script = tmp_path / "hook.sh"
        script.write_text(f"#!/bin/sh\ntouch {marker}\nexit 0\n")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)

        hook = HookConfig(event="tool.*", command=str(script))
        runner = HookRunner(hooks=(hook,))
        runner.fire("session_start", {})
        assert not marker.exists()

    def test_session_glob_matches_session_save(self, tmp_path):
        marker = tmp_path / "ran.txt"
        script = tmp_path / "hook.sh"
        script.write_text(f"#!/bin/sh\ntouch {marker}\nexit 0\n")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)

        hook = HookConfig(event="session.*", command=str(script))
        runner = HookRunner(hooks=(hook,))
        runner.fire("session_save", {})
        assert marker.exists()

    def test_exact_event_still_works(self, tmp_path):
        marker = tmp_path / "ran.txt"
        script = tmp_path / "hook.sh"
        script.write_text(f"#!/bin/sh\ntouch {marker}\nexit 0\n")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)

        hook = HookConfig(event="http_retry", command=str(script))
        runner = HookRunner(hooks=(hook,))
        runner.fire("http_retry", {})
        assert marker.exists()

    def test_exact_event_does_not_match_other(self, tmp_path):
        marker = tmp_path / "ran.txt"
        script = tmp_path / "hook.sh"
        script.write_text(f"#!/bin/sh\ntouch {marker}\nexit 0\n")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)

        hook = HookConfig(event="http_retry", command=str(script))
        runner = HookRunner(hooks=(hook,))
        runner.fire("http_request", {})
        assert not marker.exists()


# ---------------------------------------------------------------------------
# on_error behaviour
# ---------------------------------------------------------------------------

class TestOnError:
    def test_on_error_warn_non_zero_non_2_returns_message(self, tmp_path):
        script = tmp_path / "hook.sh"
        script.write_text("#!/bin/sh\necho 'something went wrong'\nexit 1\n")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)

        hook = HookConfig(event="pre_tool_use", command=str(script), on_error="warn")
        runner = HookRunner(hooks=(hook,))
        outcome = runner.fire("pre_tool_use", {})
        assert outcome.denied is False
        assert len(outcome.messages) > 0

    def test_on_error_deny_non_zero_non_2_denies(self, tmp_path):
        script = tmp_path / "hook.sh"
        script.write_text("#!/bin/sh\necho 'denied by on_error'\nexit 1\n")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)

        hook = HookConfig(event="pre_tool_use", command=str(script), on_error="deny")
        runner = HookRunner(hooks=(hook,))
        outcome = runner.fire("pre_tool_use", {})
        assert outcome.denied is True

    def test_on_error_ignore_non_zero_non_2_is_silent(self, tmp_path):
        script = tmp_path / "hook.sh"
        script.write_text("#!/bin/sh\nexit 3\n")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)

        hook = HookConfig(event="pre_tool_use", command=str(script), on_error="ignore")
        runner = HookRunner(hooks=(hook,))
        outcome = runner.fire("pre_tool_use", {})
        assert outcome.denied is False
        assert outcome.messages == []

    def test_exit_2_always_denies_regardless_of_on_error(self, tmp_path):
        """Exit code 2 is always deny, regardless of on_error setting."""
        script = tmp_path / "hook.sh"
        script.write_text("#!/bin/sh\nexit 2\n")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)

        for on_error in ("warn", "deny", "ignore"):
            hook = HookConfig(event="pre_tool_use", command=str(script), on_error=on_error)
            runner = HookRunner(hooks=(hook,))
            outcome = runner.fire("pre_tool_use", {})
            assert outcome.denied is True, f"on_error={on_error!r} should still deny on exit 2"


# ---------------------------------------------------------------------------
# Per-hook timeout
# ---------------------------------------------------------------------------

class TestPerHookTimeout:
    def test_hook_times_out_using_hook_timeout(self, tmp_path):
        script = tmp_path / "slow.sh"
        script.write_text("#!/bin/sh\nsleep 10\nexit 0\n")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)

        hook = HookConfig(event="pre_tool_use", command=str(script), timeout=0.1)
        runner = HookRunner(hooks=(hook,))
        outcome = runner.fire("pre_tool_use", {})
        assert outcome.denied is False
        assert any("timed out" in m.lower() or "timeout" in m.lower() for m in outcome.messages)


# ---------------------------------------------------------------------------
# New event types — fire() dispatches by event name
# ---------------------------------------------------------------------------

class TestFireDispatch:
    @pytest.mark.parametrize("event", [
        # tool group
        "pre_tool_use", "post_tool_use", "tool_error", "tool_denied",
        # command group
        "pre_command", "post_command", "command_error",
        # prompt group
        "prompt_submit", "prompt_compile", "prompt_cache_hit", "prompt_cache_miss",
        # agent group
        "agent_spawn", "agent_complete", "agent_error", "agent_message",
        # session group
        "session_start", "session_end", "session_save", "session_compact", "session_dream",
        # http group
        "http_request", "http_response", "http_error", "http_retry", "http_fallback",
    ])
    def test_event_fires_matching_hook(self, tmp_path, event):
        marker = tmp_path / f"{event.replace('.', '_')}.txt"
        script = tmp_path / "hook.sh"
        script.write_text(f"#!/bin/sh\ntouch {marker}\nexit 0\n")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)

        hook = HookConfig(event=event, command=str(script))
        runner = HookRunner(hooks=(hook,))
        runner.fire(event, {})
        assert marker.exists(), f"Hook did not run for event {event!r}"


# ---------------------------------------------------------------------------
# Environment variables passed to hooks
# ---------------------------------------------------------------------------

class TestEnvVarsV2:
    def test_hook_event_env_var_via_fire(self, tmp_path):
        marker = tmp_path / "event.txt"
        script = tmp_path / "hook.sh"
        script.write_text(f'#!/bin/sh\necho "$HOOK_EVENT" > {marker}\nexit 0\n')
        script.chmod(script.stat().st_mode | stat.S_IEXEC)

        hook = HookConfig(event="session_start", command=str(script))
        runner = HookRunner(hooks=(hook,))
        runner.fire("session_start", {})
        assert marker.read_text().strip() == "session_start"

    def test_session_id_env_var(self, tmp_path):
        marker = tmp_path / "sid.txt"
        script = tmp_path / "hook.sh"
        script.write_text(f'#!/bin/sh\necho "$HOOK_SESSION_ID" > {marker}\nexit 0\n')
        script.chmod(script.stat().st_mode | stat.S_IEXEC)

        hook = HookConfig(event="session_start", command=str(script))
        runner = HookRunner(hooks=(hook,))
        runner.fire("session_start", {"session_id": "abc-123"})
        assert marker.read_text().strip() == "abc-123"

    def test_agent_id_env_var(self, tmp_path):
        marker = tmp_path / "aid.txt"
        script = tmp_path / "hook.sh"
        script.write_text(f'#!/bin/sh\necho "$HOOK_AGENT_ID" > {marker}\nexit 0\n')
        script.chmod(script.stat().st_mode | stat.S_IEXEC)

        hook = HookConfig(event="agent_spawn", command=str(script))
        runner = HookRunner(hooks=(hook,))
        runner.fire("agent_spawn", {"agent_id": "agent-007"})
        assert marker.read_text().strip() == "agent-007"

    def test_http_url_env_var(self, tmp_path):
        marker = tmp_path / "url.txt"
        script = tmp_path / "hook.sh"
        script.write_text(f'#!/bin/sh\necho "$HOOK_HTTP_URL" > {marker}\nexit 0\n')
        script.chmod(script.stat().st_mode | stat.S_IEXEC)

        hook = HookConfig(event="http_request", command=str(script))
        runner = HookRunner(hooks=(hook,))
        runner.fire("http_request", {"url": "https://example.com/api"})
        assert marker.read_text().strip() == "https://example.com/api"

    def test_http_status_env_var(self, tmp_path):
        marker = tmp_path / "status.txt"
        script = tmp_path / "hook.sh"
        script.write_text(f'#!/bin/sh\necho "$HOOK_HTTP_STATUS" > {marker}\nexit 0\n')
        script.chmod(script.stat().st_mode | stat.S_IEXEC)

        hook = HookConfig(event="http_response", command=str(script))
        runner = HookRunner(hooks=(hook,))
        runner.fire("http_response", {"status": "200"})
        assert marker.read_text().strip() == "200"

    def test_command_env_var(self, tmp_path):
        marker = tmp_path / "cmd.txt"
        script = tmp_path / "hook.sh"
        script.write_text(f'#!/bin/sh\necho "$HOOK_COMMAND" > {marker}\nexit 0\n')
        script.chmod(script.stat().st_mode | stat.S_IEXEC)

        hook = HookConfig(event="pre_command", command=str(script))
        runner = HookRunner(hooks=(hook,))
        runner.fire("pre_command", {"command": "ls -la"})
        assert marker.read_text().strip() == "ls -la"

    def test_tool_name_via_fire_context(self, tmp_path):
        marker = tmp_path / "tool.txt"
        script = tmp_path / "hook.sh"
        script.write_text(f'#!/bin/sh\necho "$HOOK_TOOL_NAME" > {marker}\nexit 0\n')
        script.chmod(script.stat().st_mode | stat.S_IEXEC)

        hook = HookConfig(event="pre_tool_use", command=str(script))
        runner = HookRunner(hooks=(hook,))
        runner.fire("pre_tool_use", {"tool_name": "bash_exec"})
        assert marker.read_text().strip() == "bash_exec"


# ---------------------------------------------------------------------------
# Backwards compat: pre_tool_use / post_tool_use helpers still work
# ---------------------------------------------------------------------------

class TestBackwardsCompat:
    def test_pre_tool_use_still_works(self, tmp_path):
        marker = tmp_path / "ran.txt"
        script = tmp_path / "hook.sh"
        script.write_text(f"#!/bin/sh\ntouch {marker}\nexit 0\n")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)

        hook = HookConfig(event="pre_tool_use", command=str(script))
        runner = HookRunner(hooks=(hook,))
        runner.pre_tool_use("bash", {"cmd": "ls"})
        assert marker.exists()

    def test_post_tool_use_still_works(self, tmp_path):
        marker = tmp_path / "ran.txt"
        script = tmp_path / "hook.sh"
        script.write_text(f"#!/bin/sh\ntouch {marker}\nexit 0\n")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)

        from llm_code.tools.base import ToolResult
        hook = HookConfig(event="post_tool_use", command=str(script))
        runner = HookRunner(hooks=(hook,))
        runner.post_tool_use("bash", {}, ToolResult(output="ok"))
        assert marker.exists()
