"""Tests for SandboxPolicyResolver (H3 deep wire).

The resolver takes the existing ``SandboxConfig`` (Docker-specific
tunables) and the tool about to execute, and returns a
platform-agnostic :class:`SandboxPolicy` — or ``None`` when the
sandbox is disabled so callers fall back to the legacy path.
"""
from __future__ import annotations

from llm_code.sandbox.policy_manager import (
    SandboxPolicy,
    SandboxPolicyResolver,
)
from llm_code.tools.sandbox import SandboxConfig


# ---------- Disabled sandbox ----------


class TestSandboxDisabled:
    def test_returns_none_when_disabled(self) -> None:
        r = SandboxPolicyResolver(SandboxConfig(enabled=False))
        assert r.resolve_for_tool("bash", {}) is None

    def test_returns_none_for_every_tool_when_disabled(self) -> None:
        r = SandboxPolicyResolver(SandboxConfig(enabled=False))
        for tool in ("bash", "read_file", "edit_file", "web_fetch"):
            assert r.resolve_for_tool(tool, {}) is None


# ---------- Enabled sandbox ----------


class TestSandboxEnabled:
    def test_read_only_mount_returns_read_only_policy(self) -> None:
        r = SandboxPolicyResolver(
            SandboxConfig(enabled=True, network=False, mount_readonly=True),
        )
        p = r.resolve_for_tool("bash", {"command": "ls"})
        assert isinstance(p, SandboxPolicy)
        assert p.allow_read is True
        assert p.allow_write is False
        assert p.allow_network is False

    def test_workspace_mount_allows_writes(self) -> None:
        r = SandboxPolicyResolver(
            SandboxConfig(enabled=True, network=False, mount_readonly=False),
        )
        p = r.resolve_for_tool("bash", {"command": "touch x"})
        assert p.allow_write is True
        assert p.allow_network is False

    def test_network_flag_respected(self) -> None:
        r = SandboxPolicyResolver(
            SandboxConfig(enabled=True, network=True, mount_readonly=False),
        )
        p = r.resolve_for_tool("bash", {"command": "curl github.com"})
        assert p.allow_network is True


# ---------- Tool-specific overrides ----------


class TestToolSpecificOverrides:
    def test_read_file_clamped_to_read_only(self) -> None:
        """Read-only tools never need the destructive policy even when
        the sandbox config allows writes — clamp to least privilege."""
        r = SandboxPolicyResolver(
            SandboxConfig(enabled=True, network=True, mount_readonly=False),
        )
        p = r.resolve_for_tool("read_file", {"path": "/x"})
        assert p.allow_write is False
        # network stays because config allows it; resolver's job is to
        # tighten, not expand, per-tool.
        assert p.allow_network is True

    def test_grep_clamped_to_read_only(self) -> None:
        r = SandboxPolicyResolver(
            SandboxConfig(enabled=True, network=False, mount_readonly=False),
        )
        p = r.resolve_for_tool("grep_search", {"pattern": "x"})
        assert p.allow_write is False

    def test_web_fetch_requires_network(self) -> None:
        """Unrelated config says network=False, but web_fetch trivially
        needs it — the resolver flags that the tool cannot run under
        the current policy by setting allow_network=False; the runtime
        can then deny the call cleanly."""
        r = SandboxPolicyResolver(
            SandboxConfig(enabled=True, network=False, mount_readonly=False),
        )
        p = r.resolve_for_tool("web_fetch", {"url": "https://x"})
        assert p.allow_network is False


# ---------- Helpers ----------


class TestReadOnlyToolDetection:
    """The resolver's clamping uses a small curated allowlist so it
    stays deterministic even without a live tool registry."""

    def test_known_read_only_tools(self) -> None:
        from llm_code.sandbox.policy_manager import READ_ONLY_TOOL_NAMES

        assert "read_file" in READ_ONLY_TOOL_NAMES
        assert "glob_search" in READ_ONLY_TOOL_NAMES
        assert "grep_search" in READ_ONLY_TOOL_NAMES
        assert "git_status" in READ_ONLY_TOOL_NAMES

    def test_destructive_tools_not_in_readonly(self) -> None:
        from llm_code.sandbox.policy_manager import READ_ONLY_TOOL_NAMES

        assert "bash" not in READ_ONLY_TOOL_NAMES
        assert "edit_file" not in READ_ONLY_TOOL_NAMES
        assert "write_file" not in READ_ONLY_TOOL_NAMES
