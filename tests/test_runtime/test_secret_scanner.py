"""Tests for secret_scanner — redaction of leaked secrets from output."""
from __future__ import annotations

import pytest

from llm_code.runtime.secret_scanner import scan_output


class TestScanOutput:
    def test_clean_text_unchanged(self) -> None:
        text = "Hello world, nothing secret here."
        cleaned, findings = scan_output(text)
        assert cleaned == text
        assert findings == []

    def test_empty_string(self) -> None:
        cleaned, findings = scan_output("")
        assert cleaned == ""
        assert findings == []

    def test_aws_access_key_redacted(self) -> None:
        text = "key=AKIA1234567890ABCDEF rest"
        cleaned, findings = scan_output(text)
        assert "[REDACTED:aws_access_key]" in cleaned
        assert "AKIA1234567890ABCDEF" not in cleaned
        assert len(findings) == 1
        assert "aws_access_key" in findings[0]

    def test_github_pat_redacted(self) -> None:
        pat = "ghp_" + "A" * 36
        text = f"token={pat} done"
        cleaned, findings = scan_output(text)
        assert "[REDACTED:github_pat]" in cleaned
        assert pat not in cleaned
        assert len(findings) == 1
        assert "github_pat" in findings[0]

    def test_jwt_redacted(self) -> None:
        header = "eyJ" + "A" * 20
        payload = "eyJ" + "B" * 20
        sig = "C" * 22
        jwt = f"{header}.{payload}.{sig}"
        text = f"Authorization: Bearer {jwt}"
        cleaned, findings = scan_output(text)
        assert "[REDACTED:jwt]" in cleaned
        assert jwt not in cleaned
        assert any("jwt" in f for f in findings)

    def test_private_key_header_redacted(self) -> None:
        text = "-----BEGIN PRIVATE KEY-----\nMIIE..."
        cleaned, findings = scan_output(text)
        assert "[REDACTED:private_key]" in cleaned
        assert "-----BEGIN PRIVATE KEY-----" not in cleaned
        assert any("private_key" in f for f in findings)

    def test_rsa_private_key_header_redacted(self) -> None:
        text = "-----BEGIN RSA PRIVATE KEY-----\nMIIE..."
        cleaned, findings = scan_output(text)
        assert "[REDACTED:private_key]" in cleaned

    def test_slack_token_redacted(self) -> None:
        token = "xoxb-1234567890-abcdefghij"
        text = f"SLACK_TOKEN={token}"
        cleaned, findings = scan_output(text)
        assert "[REDACTED:slack_token]" in cleaned
        assert token not in cleaned
        assert any("slack_token" in f for f in findings)

    def test_generic_api_key_redacted(self) -> None:
        secret = "A" * 32
        text = f'api_key="{secret}"'
        cleaned, findings = scan_output(text)
        assert "[REDACTED:generic_api_key]" in cleaned
        assert secret not in cleaned
        assert any("generic_api_key" in f for f in findings)

    def test_multiple_secrets_all_redacted(self) -> None:
        aws_key = "AKIA1234567890ABCDEF"
        slack = "xoxb-1234567890-abcdefghij"
        text = f"aws={aws_key} slack={slack}"
        cleaned, findings = scan_output(text)
        assert aws_key not in cleaned
        assert slack not in cleaned
        assert "[REDACTED:aws_access_key]" in cleaned
        assert "[REDACTED:slack_token]" in cleaned
        assert len(findings) == 2

    def test_text_without_secrets_unchanged(self) -> None:
        text = "ls -la /tmp\ntotal 42\ndrwxr-xr-x  2 user user 4096 Jan 1 00:00 ."
        cleaned, findings = scan_output(text)
        assert cleaned == text
        assert findings == []
