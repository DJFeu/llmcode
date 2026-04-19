"""Seatbelt backend respects granular allowed_ports / allowed_cidrs.

When the policy specifies a non-empty ``allowed_ports`` or
``allowed_cidrs`` list, the seatbelt profile emits granular
``network-outbound`` rules instead of the broad ``(allow network*)``.
This lets macOS hosts actually enforce a network allowlist rather
than settle for the binary on/off we had before.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from llm_code.sandbox.policy_manager import SandboxPolicy


@pytest.fixture
def seatbelt():
    with patch("shutil.which", return_value="/usr/bin/sandbox-exec"):
        from llm_code.sandbox.seatbelt import SeatbeltSandboxBackend

        yield SeatbeltSandboxBackend(workspace="/tmp/ws")


class TestBroadNetwork:
    def test_allow_network_true_without_allowlist_emits_network_star(self, seatbelt) -> None:
        profile = seatbelt._render_profile(SandboxPolicy(allow_network=True))
        assert "(allow network*)" in profile

    def test_allow_network_false_emits_no_network_rule(self, seatbelt) -> None:
        profile = seatbelt._render_profile(SandboxPolicy(allow_network=False))
        assert "network*" not in profile


class TestPortAllowlist:
    def test_allowed_ports_emits_per_port_rule(self, seatbelt) -> None:
        policy = SandboxPolicy(allow_network=True, allowed_ports=(443, 80))
        profile = seatbelt._render_profile(policy)
        assert 'remote tcp "*:443"' in profile
        assert 'remote tcp "*:80"' in profile
        # Granular allowlist present — broad rule must NOT be emitted.
        assert "(allow network*)" not in profile

    def test_allowed_ports_without_allow_network_still_emits_rules(self, seatbelt) -> None:
        """An explicit allowlist makes sense even with allow_network=False —
        'block everything except these specific ports'."""
        policy = SandboxPolicy(allow_network=False, allowed_ports=(53,))
        profile = seatbelt._render_profile(policy)
        assert 'remote tcp "*:53"' in profile

    def test_empty_allowed_ports_emits_no_port_rule(self, seatbelt) -> None:
        policy = SandboxPolicy(allow_network=True, allowed_ports=())
        profile = seatbelt._render_profile(policy)
        assert 'remote tcp "*:' not in profile


class TestCidrAllowlist:
    def test_allowed_cidrs_emits_per_cidr_rule(self, seatbelt) -> None:
        policy = SandboxPolicy(
            allow_network=True, allowed_cidrs=("10.0.0.0/8", "192.168.1.0/24"),
        )
        profile = seatbelt._render_profile(policy)
        assert "10.0.0.0/8" in profile
        assert "192.168.1.0/24" in profile

    def test_cidr_with_port_combines(self, seatbelt) -> None:
        """Combining ports + CIDRs should emit both rule families."""
        policy = SandboxPolicy(
            allow_network=True,
            allowed_ports=(443,),
            allowed_cidrs=("10.0.0.0/8",),
        )
        profile = seatbelt._render_profile(policy)
        assert 'remote tcp "*:443"' in profile
        assert "10.0.0.0/8" in profile
