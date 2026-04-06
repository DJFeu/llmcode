"""Test secret scanning integration for TUI output."""
from __future__ import annotations

from llm_code.runtime.secret_scanner import scan_output


class TestSecretRedaction:
    def test_aws_key_redacted_in_bash_context(self) -> None:
        """AWS key in typical env output should be redacted."""
        output = "AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE\nAWS_SECRET=secret"
        cleaned, findings = scan_output(output)
        assert "AKIAIOSFODNN7EXAMPLE" not in cleaned
        assert "[REDACTED:" in cleaned
        assert len(findings) >= 1

    def test_github_pat_in_git_config(self) -> None:
        """GitHub PAT in git remote URL should be redacted."""
        pat = "ghp_" + "a" * 36
        output = f"remote.origin.url=https://{pat}@github.com/user/repo"
        cleaned, findings = scan_output(output)
        assert "ghp_" not in cleaned
        assert "[REDACTED:github_pat]" in cleaned

    def test_github_pat_variants(self) -> None:
        """All GitHub PAT prefixes should be caught."""
        for prefix in ("ghp_", "gho_", "ghu_", "ghs_", "ghr_"):
            pat = prefix + "A" * 36
            output = f"token={pat}"
            cleaned, findings = scan_output(output)
            assert pat not in cleaned, f"{prefix} PAT was not redacted"

    def test_clean_build_output_unchanged(self) -> None:
        """Normal build output should pass through unchanged."""
        output = "Building project...\n[OK] 42 tests passed\nDone in 3.2s"
        cleaned, findings = scan_output(output)
        assert cleaned == output
        assert findings == []

    def test_jwt_redacted(self) -> None:
        """JWT token should be redacted."""
        header = "eyJ" + "A" * 20
        payload = "eyJ" + "B" * 20
        sig = "C" * 22
        jwt = f"{header}.{payload}.{sig}"
        output = f"Authorization: Bearer {jwt}"
        cleaned, findings = scan_output(output)
        assert jwt not in cleaned
        assert "[REDACTED:jwt]" in cleaned

    def test_private_key_header_redacted(self) -> None:
        """Private key header should be redacted."""
        output = "-----BEGIN PRIVATE KEY-----\nMIIE..."
        cleaned, findings = scan_output(output)
        assert "BEGIN PRIVATE KEY" not in cleaned
        assert "[REDACTED:private_key]" in cleaned

    def test_multiple_secrets_all_redacted(self) -> None:
        """Multiple different secrets should all be redacted."""
        aws_key = "AKIA1234567890ABCDEF"
        pat = "ghp_" + "X" * 36
        output = f"key={aws_key}\ntoken={pat}"
        cleaned, findings = scan_output(output)
        assert aws_key not in cleaned
        assert pat not in cleaned
        assert len(findings) >= 2

    def test_slack_token_redacted(self) -> None:
        """Slack tokens should be redacted."""
        output = "SLACK_TOKEN=xoxb-1234567890-abcdefghij"
        cleaned, findings = scan_output(output)
        assert "xoxb-" not in cleaned
        assert "[REDACTED:slack_token]" in cleaned
