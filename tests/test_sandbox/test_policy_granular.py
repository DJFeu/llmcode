"""SandboxPolicy granular network allowlist + JSON-schema loader.

Previous policy was filesystem-only: ``allow_paths`` / ``deny_paths``.
Network was a single boolean ``allow_network``. This commit adds:

    * ``allowed_ports``  — outbound TCP/UDP ports permitted
    * ``allowed_cidrs``  — outbound CIDR ranges permitted
    * ``policy_from_dict`` / ``policy_from_json`` declarative loader
    * ``policy_to_dict`` for round-trip serialization

Empty tuples preserve the existing semantics so every older call
site stays untouched.
"""
from __future__ import annotations

import json

import pytest

from llm_code.sandbox.policy_manager import SandboxPolicy
from llm_code.sandbox.policy_schema import (
    PolicySchemaError,
    policy_from_dict,
    policy_from_json,
    policy_to_dict,
)


class TestNewFields:
    def test_defaults_stay_empty(self) -> None:
        p = SandboxPolicy()
        assert p.allowed_ports == ()
        assert p.allowed_cidrs == ()

    def test_tuple_not_list(self) -> None:
        """Frozen dataclass needs hashable members — tuples, not lists."""
        p = SandboxPolicy(allowed_ports=(80, 443), allowed_cidrs=("10.0.0.0/8",))
        assert isinstance(p.allowed_ports, tuple)
        assert isinstance(p.allowed_cidrs, tuple)

    def test_policy_is_still_hashable(self) -> None:
        p = SandboxPolicy(allowed_ports=(80,), allowed_cidrs=("192.168.0.0/16",))
        {p}  # adding to a set exercises __hash__ end-to-end


class TestPolicyFromDict:
    def test_minimal_dict(self) -> None:
        p = policy_from_dict({})
        assert p == SandboxPolicy()

    def test_every_field(self) -> None:
        p = policy_from_dict({
            "allow_read": True,
            "allow_write": True,
            "allow_network": False,
            "allow_paths": ["/workspace"],
            "deny_paths": ["/etc/secrets"],
            "allowed_ports": [80, 443],
            "allowed_cidrs": ["10.0.0.0/8", "192.168.1.0/24"],
        })
        assert p.allow_write is True
        assert p.allow_paths == ("/workspace",)
        assert p.deny_paths == ("/etc/secrets",)
        assert p.allowed_ports == (80, 443)
        assert p.allowed_cidrs == ("10.0.0.0/8", "192.168.1.0/24")

    def test_lists_are_converted_to_tuples(self) -> None:
        """JSON has no tuples; the loader must convert."""
        p = policy_from_dict({"allow_paths": ["/a", "/b"]})
        assert isinstance(p.allow_paths, tuple)
        assert p.allow_paths == ("/a", "/b")

    def test_rejects_unknown_field(self) -> None:
        with pytest.raises(PolicySchemaError, match="unknown"):
            policy_from_dict({"allow_everything": True})

    def test_rejects_port_out_of_range(self) -> None:
        with pytest.raises(PolicySchemaError, match="port"):
            policy_from_dict({"allowed_ports": [0]})
        with pytest.raises(PolicySchemaError, match="port"):
            policy_from_dict({"allowed_ports": [70000]})

    def test_rejects_non_integer_port(self) -> None:
        with pytest.raises(PolicySchemaError, match="port"):
            policy_from_dict({"allowed_ports": ["80"]})

    def test_rejects_malformed_cidr(self) -> None:
        with pytest.raises(PolicySchemaError, match="cidr|CIDR"):
            policy_from_dict({"allowed_cidrs": ["not-a-cidr"]})

    def test_accepts_single_host_cidr(self) -> None:
        # /32 for IPv4 and /128 for IPv6 are valid single-host CIDRs.
        p = policy_from_dict({"allowed_cidrs": ["10.0.0.1/32", "2001:db8::1/128"]})
        assert p.allowed_cidrs == ("10.0.0.1/32", "2001:db8::1/128")

    def test_rejects_wrong_type_for_bool_field(self) -> None:
        with pytest.raises(PolicySchemaError, match="bool"):
            policy_from_dict({"allow_write": "yes"})


class TestPolicyToDict:
    def test_roundtrip_preserves_fields(self) -> None:
        p = SandboxPolicy(
            allow_write=True,
            allow_network=True,
            allow_paths=("/workspace",),
            allowed_ports=(443,),
            allowed_cidrs=("10.0.0.0/8",),
        )
        round_tripped = policy_from_dict(policy_to_dict(p))
        assert round_tripped == p

    def test_to_dict_is_json_serializable(self) -> None:
        p = SandboxPolicy(allowed_ports=(80, 443), allowed_cidrs=("10.0.0.0/8",))
        text = json.dumps(policy_to_dict(p))
        reloaded = policy_from_dict(json.loads(text))
        assert reloaded == p


class TestPolicyFromJson:
    def test_load_from_file(self, tmp_path) -> None:
        path = tmp_path / "policy.json"
        path.write_text(json.dumps({
            "allow_write": True,
            "allowed_ports": [443],
            "allowed_cidrs": ["10.0.0.0/8"],
        }))
        p = policy_from_json(path)
        assert p.allow_write is True
        assert p.allowed_ports == (443,)
        assert p.allowed_cidrs == ("10.0.0.0/8",)

    def test_load_from_malformed_json(self, tmp_path) -> None:
        path = tmp_path / "policy.json"
        path.write_text("{not valid")
        with pytest.raises(PolicySchemaError, match="JSON|parse"):
            policy_from_json(path)
