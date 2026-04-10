"""Tests for sandbox denial parser."""
from __future__ import annotations

from llm_code.runtime.denial_parser import (
    DenialInfo,
    format_denial_hint,
    parse_denial,
)


class TestParseDenial:
    def test_permission_denied(self) -> None:
        info = parse_denial("bash: /etc/shadow: Permission denied")
        assert info is not None
        assert info.blocked_path == "/etc/shadow"
        assert info.permission_type == "write"

    def test_eacces(self) -> None:
        info = parse_denial("EACCES: permission denied, open '/var/log/app.log'")
        assert info is not None
        assert info.blocked_path == "/var/log/app.log"

    def test_python_permission_error(self) -> None:
        info = parse_denial("PermissionError: [Errno 13] Permission denied: '/root/.ssh/id_rsa'")
        assert info is not None
        assert info.blocked_path == "/root/.ssh/id_rsa"

    def test_network_refused(self) -> None:
        info = parse_denial("Connection refused to 192.168.1.100:5432")
        assert info is not None
        assert info.permission_type == "network"

    def test_npm_eacces(self) -> None:
        info = parse_denial("npm ERR! EACCES: permission denied, mkdir '/usr/local/lib/node_modules'")
        assert info is not None
        assert info.blocked_path == "/usr/local/lib/node_modules"

    def test_no_denial(self) -> None:
        assert parse_denial("File not found") is None

    def test_empty_stderr(self) -> None:
        assert parse_denial("") is None

    def test_caching(self) -> None:
        """Same input returns cached result."""
        info1 = parse_denial("Permission denied: /test")
        info2 = parse_denial("Permission denied: /test")
        assert info1 is info2  # same object from cache

    def test_docker_permission(self) -> None:
        info = parse_denial("Got permission denied while trying to connect to the Docker daemon socket at /var/run/docker.sock")
        assert info is not None
        assert info.permission_type == "execute"

    def test_sandbox_deny(self) -> None:
        info = parse_denial("sandbox: Operation not permitted /usr/local/bin/thing")
        assert info is not None

    def test_suggestion_included(self) -> None:
        info = parse_denial("Permission denied: /tmp/test")
        assert info is not None
        assert "/tmp/test" in info.suggestion


class TestFormatDenialHint:
    def test_format_with_path(self) -> None:
        info = DenialInfo(
            blocked_path="/etc/shadow",
            permission_type="write",
            raw_error="Permission denied",
            suggestion="Grant write access to: /etc/shadow",
        )
        hint = format_denial_hint(info)
        assert "write" in hint
        assert "/etc/shadow" in hint

    def test_format_network(self) -> None:
        info = DenialInfo(
            blocked_path="192.168.1.1:5432",
            permission_type="network",
            raw_error="Connection refused",
            suggestion="Allow network access to: 192.168.1.1:5432",
        )
        hint = format_denial_hint(info)
        assert "network" in hint
