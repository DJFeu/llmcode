"""Declarative ``SandboxPolicy`` loader (JSON / dict).

The previous API only accepted a :class:`SandboxPolicy` dataclass
instance, so callers hand-rolled their policies in Python. This module
lets callers declare policies in configuration files and load them in
via ``policy_from_dict`` / ``policy_from_json`` with a typed validator
that produces actionable :class:`PolicySchemaError` messages instead
of obscure dataclass construction failures.
"""
from __future__ import annotations

import ipaddress
import json
from pathlib import Path
from typing import Any

from llm_code.sandbox.policy_manager import SandboxPolicy


_BOOL_FIELDS = ("allow_read", "allow_write", "allow_network")
_PATH_FIELDS = ("allow_paths", "deny_paths")
_KNOWN_FIELDS = frozenset(
    _BOOL_FIELDS + _PATH_FIELDS + ("allowed_ports", "allowed_cidrs")
)


class PolicySchemaError(ValueError):
    """Raised when the input dict/JSON fails schema validation."""


def policy_from_dict(data: dict[str, Any]) -> SandboxPolicy:
    """Build a :class:`SandboxPolicy` from a plain dict, validating fields.

    Raises :class:`PolicySchemaError` on unknown fields, wrong types,
    out-of-range ports, or malformed CIDRs.
    """
    unknown = set(data) - _KNOWN_FIELDS
    if unknown:
        raise PolicySchemaError(
            f"unknown field(s): {sorted(unknown)}"
        )

    kwargs: dict[str, Any] = {}
    for field in _BOOL_FIELDS:
        if field in data:
            value = data[field]
            if not isinstance(value, bool):
                raise PolicySchemaError(
                    f"{field!r} must be bool, got {type(value).__name__}"
                )
            kwargs[field] = value

    for field in _PATH_FIELDS:
        if field in data:
            kwargs[field] = _coerce_str_tuple(field, data[field])

    if "allowed_ports" in data:
        kwargs["allowed_ports"] = _coerce_ports(data["allowed_ports"])
    if "allowed_cidrs" in data:
        kwargs["allowed_cidrs"] = _coerce_cidrs(data["allowed_cidrs"])

    return SandboxPolicy(**kwargs)


def policy_to_dict(policy: SandboxPolicy) -> dict[str, Any]:
    """Inverse of :func:`policy_from_dict` — returns a JSON-safe dict."""
    return {
        "allow_read": policy.allow_read,
        "allow_write": policy.allow_write,
        "allow_network": policy.allow_network,
        "allow_paths": list(policy.allow_paths),
        "deny_paths": list(policy.deny_paths),
        "allowed_ports": list(policy.allowed_ports),
        "allowed_cidrs": list(policy.allowed_cidrs),
    }


def policy_from_json(path: Path | str) -> SandboxPolicy:
    """Load a policy from a JSON file on disk."""
    text = Path(path).read_text()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise PolicySchemaError(f"failed to parse JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise PolicySchemaError("policy JSON must be an object at top level")
    return policy_from_dict(data)


# ── helpers ──────────────────────────────────────────────────────────


def _coerce_str_tuple(field: str, value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise PolicySchemaError(
            f"{field!r} must be a list of strings, got {type(value).__name__}"
        )
    out: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise PolicySchemaError(
                f"{field!r} entries must be strings, got {type(item).__name__}"
            )
        out.append(item)
    return tuple(out)


def _coerce_ports(value: Any) -> tuple[int, ...]:
    if not isinstance(value, (list, tuple)):
        raise PolicySchemaError(
            f"'allowed_ports' must be a list of ints, got {type(value).__name__}"
        )
    out: list[int] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int):
            raise PolicySchemaError(
                f"'allowed_ports' entries must be integer port numbers, "
                f"got {type(item).__name__}"
            )
        if not (1 <= item <= 65535):
            raise PolicySchemaError(
                f"'allowed_ports' entry {item} out of range 1–65535"
            )
        out.append(item)
    return tuple(out)


def _coerce_cidrs(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise PolicySchemaError(
            f"'allowed_cidrs' must be a list of CIDR strings, "
            f"got {type(value).__name__}"
        )
    out: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise PolicySchemaError(
                f"'allowed_cidrs' entries must be strings, "
                f"got {type(item).__name__}"
            )
        try:
            ipaddress.ip_network(item, strict=False)
        except ValueError as exc:
            raise PolicySchemaError(
                f"'allowed_cidrs' entry {item!r} is not a valid CIDR: {exc}"
            ) from exc
        out.append(item)
    return tuple(out)
