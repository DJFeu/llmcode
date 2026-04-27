"""Tests for v16 M7 subagent flex (wildcard tools, args allowlist, inline MCP).

Covers:

* Wildcard expansion: ``read_*`` against the parent's tool surface.
* Args allowlist: ``bash:git status,git diff`` accepts matching args
  and rejects others at call time.
* Built-in policies (``read-only`` / ``build`` / ``verify`` /
  ``unrestricted``) expand to documented tool sets.
* Inline MCP server lifecycle: spawn → tracked → SIGTERM teardown.
* Crash mid-spawn doesn't leak processes.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from llm_code.runtime.subagent_factory import (
    InlineMcpRegistry,
    _ArgsAllowlistTool,
)
from llm_code.runtime.tool_policy import (
    BUILTIN_POLICIES,
    ToolSpec,
    args_allowlist_check,
    expand_policy,
    match_wildcard,
    parse_tool_entry,
    resolve_tool_subset,
)


# ---------------------------------------------------------------------------
# parse_tool_entry / match_wildcard
# ---------------------------------------------------------------------------


class TestParseToolEntry:
    def test_literal(self) -> None:
        spec = parse_tool_entry("read_file")
        assert spec == ToolSpec(name="read_file")
        assert not spec.is_wildcard

    def test_wildcard(self) -> None:
        spec = parse_tool_entry("read_*")
        assert spec.is_wildcard
        assert spec.args_allowlist == ()

    def test_args_allowlist(self) -> None:
        spec = parse_tool_entry("bash:git status,git diff,git log")
        assert spec.name == "bash"
        assert spec.args_allowlist == ("git status", "git diff", "git log")

    def test_trailing_colon(self) -> None:
        spec = parse_tool_entry("bash:")
        assert spec.name == "bash"
        assert spec.args_allowlist == ()

    def test_whitespace_trimmed(self) -> None:
        spec = parse_tool_entry("  bash  :  git status  , git diff  ")
        assert spec.name == "bash"
        assert spec.args_allowlist == ("git status", "git diff")

    def test_empty_string(self) -> None:
        spec = parse_tool_entry("")
        assert spec.name == ""


class TestMatchWildcard:
    def test_literal_match(self) -> None:
        assert match_wildcard("read_file", "read_file") is True
        assert match_wildcard("read_file", "write_file") is False

    def test_star_match(self) -> None:
        assert match_wildcard("read_*", "read_file") is True
        assert match_wildcard("read_*", "read_dir") is True
        assert match_wildcard("read_*", "read") is False  # no underscore tail

    def test_anchored_at_start(self) -> None:
        # ``read_*`` should not match a tool that merely contains ``read_``.
        assert match_wildcard("read_*", "fake_read_file") is False

    def test_question_mark(self) -> None:
        assert match_wildcard("read?", "reads") is True
        assert match_wildcard("read?", "readss") is False


# ---------------------------------------------------------------------------
# args_allowlist_check
# ---------------------------------------------------------------------------


class TestArgsAllowlist:
    def test_empty_allowlist_passes(self) -> None:
        assert args_allowlist_check("bash", {"command": "rm -rf /"}, ()) is True

    def test_starts_with_match(self) -> None:
        assert args_allowlist_check(
            "bash", {"command": "git status"}, ("git status", "git diff"),
        ) is True

    def test_starts_with_extension(self) -> None:
        # "git status --short" still matches "git status".
        assert args_allowlist_check(
            "bash", {"command": "git status --short"}, ("git status",),
        ) is True

    def test_rejected_command(self) -> None:
        assert args_allowlist_check(
            "bash", {"command": "rm -rf /"}, ("git status", "git diff"),
        ) is False

    def test_no_string_arg(self) -> None:
        # Tools without a string-typed arg fall through to True.
        assert args_allowlist_check(
            "weird_tool", {"x": 1, "y": 2}, ("foo",),
        ) is True


# ---------------------------------------------------------------------------
# Built-in policies
# ---------------------------------------------------------------------------


class TestBuiltinPolicies:
    def test_all_policies_present(self) -> None:
        assert set(BUILTIN_POLICIES.keys()) == {
            "read-only", "build", "verify", "unrestricted",
        }

    def test_read_only_excludes_bash_and_edit(self) -> None:
        patterns = BUILTIN_POLICIES["read-only"]
        # No bash, no edit_*, no write_* in read-only.
        assert "bash" not in patterns
        assert "edit_*" not in patterns
        assert "write_*" not in patterns

    def test_build_includes_edit_and_bash(self) -> None:
        patterns = BUILTIN_POLICIES["build"]
        assert "edit_*" in patterns
        assert "bash" in patterns

    def test_verify_has_bash_but_no_edit(self) -> None:
        patterns = BUILTIN_POLICIES["verify"]
        assert "bash" in patterns
        assert "edit_*" not in patterns
        assert "write_*" not in patterns

    def test_unrestricted_is_star(self) -> None:
        assert BUILTIN_POLICIES["unrestricted"] == ("*",)

    def test_expand_policy_unknown_returns_empty(self) -> None:
        assert expand_policy("does-not-exist") == ()

    def test_expand_policy_empty_string_returns_empty(self) -> None:
        assert expand_policy("") == ()
        assert expand_policy(None) == ()


# ---------------------------------------------------------------------------
# resolve_tool_subset
# ---------------------------------------------------------------------------


class TestResolveToolSubset:
    @pytest.fixture()
    def parent_tools(self) -> frozenset[str]:
        return frozenset({
            "read_file", "read_dir", "read_only_token",
            "grep_search", "glob_search",
            "bash", "edit_file", "write_file", "multi_edit",
            "git_status", "git_diff", "git_log",
            "lsp_goto_definition", "lsp_diagnostics",
        })

    def test_explicit_literals(self, parent_tools: frozenset[str]) -> None:
        names, args = resolve_tool_subset(
            parent_tools,
            explicit_tools=("read_file", "grep_search"),
        )
        assert names == frozenset({"read_file", "grep_search"})
        assert args == {}

    def test_wildcards_expand(self, parent_tools: frozenset[str]) -> None:
        names, _ = resolve_tool_subset(
            parent_tools, explicit_tools=("read_*",),
        )
        # Anchored wildcard — does NOT include "read_only_token" because
        # fnmatch is case-sensitive and the tail differs.
        # Actually read_* matches read_only_token because * matches
        # "only_token". The collision case in the spec is about
        # "read_X" not matching "fake_read_X" (start anchor).
        assert "read_file" in names
        assert "read_dir" in names

    def test_start_anchor_prevents_collision(
        self, parent_tools: frozenset[str],
    ) -> None:
        # "read_*" should not absorb a tool merely containing "read_".
        # We add a synthetic collision case.
        parent = parent_tools | {"fake_read_thing"}
        names, _ = resolve_tool_subset(
            parent, explicit_tools=("read_*",),
        )
        assert "fake_read_thing" not in names

    def test_args_allowlist_captured(
        self, parent_tools: frozenset[str],
    ) -> None:
        names, args = resolve_tool_subset(
            parent_tools, explicit_tools=("bash:git status,git diff",),
        )
        assert "bash" in names
        assert args["bash"] == ("git status", "git diff")

    def test_policy_expansion(self, parent_tools: frozenset[str]) -> None:
        names, _ = resolve_tool_subset(parent_tools, policy="read-only")
        # Read-only has read_*, grep_*, glob_*, git_status/diff/log,
        # lsp_*. Should NOT include bash / edit_* / write_*.
        assert "read_file" in names
        assert "grep_search" in names
        assert "glob_search" in names
        assert "git_status" in names
        assert "lsp_goto_definition" in names
        assert "bash" not in names
        assert "edit_file" not in names
        assert "write_file" not in names

    def test_policy_plus_explicit_unions(
        self, parent_tools: frozenset[str],
    ) -> None:
        # Adding a literal augments the policy.
        names, _ = resolve_tool_subset(
            parent_tools,
            policy="read-only",
            explicit_tools=("bash",),
        )
        assert "bash" in names
        assert "read_file" in names


# ---------------------------------------------------------------------------
# _ArgsAllowlistTool wrapper
# ---------------------------------------------------------------------------


class TestArgsAllowlistTool:
    @pytest.mark.asyncio
    async def test_passes_through_when_args_match(self) -> None:
        underlying = MagicMock()
        underlying.name = "bash"
        underlying.description = "Run a bash command"

        async def fake_call(**kwargs: Any) -> Any:
            return "OK"

        underlying.call = fake_call
        wrapped = _ArgsAllowlistTool(
            underlying, ("git status",), args_allowlist_check,
        )
        result = await wrapped.call(command="git status --short")
        assert result == "OK"

    @pytest.mark.asyncio
    async def test_rejects_when_args_dont_match(self) -> None:
        underlying = MagicMock()
        underlying.name = "bash"
        underlying.description = "Run a bash command"

        async def fake_call(**kwargs: Any) -> Any:
            return "OK"

        underlying.call = fake_call
        wrapped = _ArgsAllowlistTool(
            underlying, ("git status",), args_allowlist_check,
        )
        result = await wrapped.call(command="rm -rf /")
        # ToolResult-shaped rejection
        assert hasattr(result, "is_error")
        assert result.is_error is True
        assert "policy" in result.output

    def test_execute_passes_when_args_match(self) -> None:
        from llm_code.tools.base import ToolResult

        underlying = MagicMock()
        underlying.name = "bash"
        underlying.description = "Run a bash command"
        underlying.execute = MagicMock(
            return_value=ToolResult(output="ok"),
        )
        wrapped = _ArgsAllowlistTool(
            underlying, ("git status",), args_allowlist_check,
        )
        result = wrapped.execute({"command": "git status --short"})
        assert result.output == "ok"
        assert result.is_error is False
        underlying.execute.assert_called_once()

    def test_execute_rejects_when_args_dont_match(self) -> None:
        underlying = MagicMock()
        underlying.name = "bash"
        underlying.description = "Run a bash command"
        underlying.execute = MagicMock()
        wrapped = _ArgsAllowlistTool(
            underlying, ("git status",), args_allowlist_check,
        )
        result = wrapped.execute({"command": "rm -rf /"})
        assert result.is_error is True
        underlying.execute.assert_not_called()

    def test_wrapper_preserves_name(self) -> None:
        underlying = MagicMock()
        underlying.name = "bash"
        underlying.description = "Bash"
        wrapped = _ArgsAllowlistTool(underlying, ("git",), args_allowlist_check)
        assert wrapped.name == "bash"
        assert wrapped.description == "Bash"


# ---------------------------------------------------------------------------
# Inline MCP lifecycle
# ---------------------------------------------------------------------------


class TestInlineMcpLifecycle:
    def test_spawn_tracks_pid(self) -> None:
        registry = InlineMcpRegistry()
        # Spawn a long-lived sleep.
        registry.spawn("sleeper", sys.executable, ("-c", "import time; time.sleep(30)"))
        assert len(registry._processes) == 1
        name, wrapper = registry._processes[0]
        assert name == "sleeper"
        # Process is alive immediately after spawn.
        assert wrapper.proc.poll() is None
        registry.shutdown_all()

    def test_shutdown_terminates_processes(self) -> None:
        registry = InlineMcpRegistry()
        registry.spawn("a", sys.executable, ("-c", "import time; time.sleep(60)"))
        registry.spawn("b", sys.executable, ("-c", "import time; time.sleep(60)"))
        registry.shutdown_all()
        # No processes still in the registry after shutdown.
        assert registry._processes == []

    def test_shutdown_no_op_when_empty(self) -> None:
        registry = InlineMcpRegistry()
        registry.shutdown_all()  # no exception

    def test_spawn_failure_raises(self) -> None:
        registry = InlineMcpRegistry()
        with pytest.raises(RuntimeError):
            registry.spawn("missing", "/nonexistent/binary-that-does-not-exist", ())

    def test_signal_term_grace_then_kill(self) -> None:
        # Spawn a process that ignores SIGTERM so the SIGKILL fallback
        # path fires. Use a Python subprocess that traps SIGTERM.
        registry = InlineMcpRegistry()
        ignore_term_script = (
            "import signal, time;"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN);"
            "time.sleep(60)"
        )
        registry.spawn("stubborn", sys.executable, ("-c", ignore_term_script))
        # Shrink the grace period so the test runs in seconds.
        registry._SIGTERM_GRACE_SECONDS = 0.5
        start = time.monotonic()
        registry.shutdown_all()
        elapsed = time.monotonic() - start
        # Should have escalated to SIGKILL (well under 30s).
        assert elapsed < 5.0
        assert registry._processes == []


# ---------------------------------------------------------------------------
# Frontmatter parser picks up M7 fields
# ---------------------------------------------------------------------------


class TestFrontmatterFields:
    def test_role_with_wildcards(self, tmp_path: Path) -> None:
        from llm_code.tools.agent_loader import _load_agents_from_dir

        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        (agents_dir / "researcher.md").write_text(
            "---\n"
            "name: researcher\n"
            "description: Read-only researcher\n"
            "tools:\n"
            "  - read_*\n"
            "  - grep_*\n"
            "  - bash:git status,git diff\n"
            "---\n"
            "Body"
        )
        roles = _load_agents_from_dir(agents_dir)
        assert "researcher" in roles
        role = roles["researcher"]
        assert "read_*" in role.tool_specs
        assert "grep_*" in role.tool_specs
        assert "bash:git status,git diff" in role.tool_specs
        # Dynamic shape — allowed_tools sentinel = None.
        assert role.allowed_tools is None

    def test_role_with_policy(self, tmp_path: Path) -> None:
        from llm_code.tools.agent_loader import _load_agents_from_dir

        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        (agents_dir / "verifier.md").write_text(
            "---\n"
            "name: verifier\n"
            "description: Verifier\n"
            "tool_policy: verify\n"
            "---\n"
            "Body"
        )
        roles = _load_agents_from_dir(agents_dir)
        assert roles["verifier"].tool_policy == "verify"

    def test_role_with_inline_mcp(self, tmp_path: Path) -> None:
        from llm_code.tools.agent_loader import _load_agents_from_dir

        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        (agents_dir / "browser.md").write_text(
            "---\n"
            "name: browser\n"
            "description: Browser-aware agent\n"
            "mcp_servers:\n"
            "  - name: web\n"
            "    command: npx\n"
            "    args:\n"
            "      - '-y'\n"
            "      - '@modelcontextprotocol/server-puppeteer'\n"
            "---\n"
            "Body"
        )
        roles = _load_agents_from_dir(agents_dir)
        assert "browser" in roles
        mcp = roles["browser"].inline_mcp_servers
        assert len(mcp) == 1
        assert mcp[0][0] == "web"
        assert mcp[0][1] == "npx"
        assert mcp[0][2] == ("-y", "@modelcontextprotocol/server-puppeteer")
