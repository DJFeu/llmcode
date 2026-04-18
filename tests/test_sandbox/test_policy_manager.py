"""Tests for the sandbox policy manager (H3 — Sprint 3).

Skeleton for the Codex-inspired sandbox policy layer. Provides a
declarative ``SandboxPolicy`` type + a ``SandboxBackend`` Protocol so
future adapters (bwrap / landlock / seatbelt / Docker) can slot in
behind one interface. Does not touch the existing thin
``pty_runner`` / ``docker_sandbox`` wrappers — those stay the default
path until H3b wires this layer into the runtime.
"""
from __future__ import annotations

import pytest

from llm_code.sandbox.policy_manager import (
    SandboxBackend,
    SandboxPolicy,
    SandboxResult,
    choose_backend,
    default_policy,
)


class TestSandboxPolicy:
    def test_frozen(self) -> None:
        p = SandboxPolicy(allow_read=True)
        with pytest.raises(Exception):
            p.allow_read = False  # type: ignore[misc]

    def test_strict_defaults(self) -> None:
        """Default policy is least privilege — read-only filesystem,
        no network, no write paths. Callers must opt in."""
        p = SandboxPolicy()
        assert p.allow_read is True      # bash needs to read to do anything
        assert p.allow_write is False
        assert p.allow_network is False
        assert p.allow_paths == ()
        assert p.deny_paths == ()

    def test_full_construction(self) -> None:
        p = SandboxPolicy(
            allow_read=True,
            allow_write=True,
            allow_network=True,
            allow_paths=("/workspace",),
            deny_paths=("/etc", "/home/user/.ssh"),
        )
        assert p.allow_write is True
        assert p.allow_network is True
        assert "/workspace" in p.allow_paths


class TestDefaultPolicy:
    def test_returns_read_only(self) -> None:
        p = default_policy()
        assert p.allow_write is False
        assert p.allow_network is False

    def test_workspace_mode(self) -> None:
        p = default_policy(mode="workspace")
        assert p.allow_write is True
        assert p.allow_network is False

    def test_full_access_mode(self) -> None:
        p = default_policy(mode="full_access")
        assert p.allow_write is True
        assert p.allow_network is True


class TestSandboxBackendProtocol:
    def test_stub_backend_satisfies_protocol(self) -> None:
        """The Protocol is satisfied structurally — any object exposing
        .name and .execute qualifies."""

        class StubBackend:
            name = "stub"

            def execute(
                self, command: list[str], policy: SandboxPolicy,
            ) -> SandboxResult:
                return SandboxResult(exit_code=0, stdout="", stderr="")

        backend = StubBackend()
        assert isinstance(backend, SandboxBackend)
        result = backend.execute(["echo", "hi"], default_policy())
        assert result.exit_code == 0


class TestSandboxResult:
    def test_frozen(self) -> None:
        r = SandboxResult(exit_code=0, stdout="", stderr="")
        with pytest.raises(Exception):
            r.exit_code = 1  # type: ignore[misc]

    def test_success_helper(self) -> None:
        assert SandboxResult(exit_code=0, stdout="", stderr="").is_success is True
        assert SandboxResult(exit_code=1, stdout="", stderr="x").is_success is False


class TestChooseBackend:
    def test_returns_something_for_known_platform(self) -> None:
        """The selector must pick an adapter on a supported platform;
        the exact class depends on the OS. We just check it returned
        *something* that satisfies SandboxBackend so the runtime can
        keep executing."""
        backend = choose_backend()
        assert isinstance(backend, SandboxBackend)
        assert backend.name in {"pty", "docker", "bwrap", "seatbelt", "null"}
