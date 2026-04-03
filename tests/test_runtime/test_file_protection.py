"""Tests for llm_code.runtime.file_protection."""
from __future__ import annotations

import os
import pytest

from llm_code.runtime.file_protection import (
    SENSITIVE_PATTERNS,
    FileProtectionResult,
    check_read,
    check_write,
    is_sensitive,
)


# ---------------------------------------------------------------------------
# FileProtectionResult
# ---------------------------------------------------------------------------

class TestFileProtectionResult:
    def test_is_frozen(self):
        result = FileProtectionResult(allowed=True, reason="ok", severity="allow")
        with pytest.raises((AttributeError, TypeError)):
            result.allowed = False  # type: ignore[misc]

    def test_allow_result(self):
        r = FileProtectionResult(allowed=True, reason="", severity="allow")
        assert r.allowed is True
        assert r.severity == "allow"

    def test_warn_result(self):
        r = FileProtectionResult(allowed=True, reason="careful", severity="warn")
        assert r.allowed is True
        assert r.severity == "warn"

    def test_block_result(self):
        r = FileProtectionResult(allowed=False, reason="blocked", severity="block")
        assert r.allowed is False
        assert r.severity == "block"


# ---------------------------------------------------------------------------
# SENSITIVE_PATTERNS contains expected entries
# ---------------------------------------------------------------------------

class TestSensitivePatterns:
    def test_contains_dotenv(self):
        assert ".env" in SENSITIVE_PATTERNS

    def test_contains_dotenv_star(self):
        assert ".env.*" in SENSITIVE_PATTERNS

    def test_contains_key_glob(self):
        assert "*.key" in SENSITIVE_PATTERNS

    def test_contains_pem(self):
        assert "*.pem" in SENSITIVE_PATTERNS

    def test_contains_p12(self):
        assert "*.p12" in SENSITIVE_PATTERNS

    def test_contains_credentials_glob(self):
        assert "credentials.*" in SENSITIVE_PATTERNS

    def test_contains_secret_glob(self):
        assert "*secret*" in SENSITIVE_PATTERNS

    def test_contains_id_rsa(self):
        assert "id_rsa" in SENSITIVE_PATTERNS

    def test_contains_id_ed25519(self):
        assert "id_ed25519" in SENSITIVE_PATTERNS

    def test_contains_token_json(self):
        assert "token.json" in SENSITIVE_PATTERNS

    def test_contains_keystore(self):
        assert "*.keystore" in SENSITIVE_PATTERNS

    def test_contains_netrc(self):
        assert ".netrc" in SENSITIVE_PATTERNS

    def test_contains_pgpass(self):
        assert ".pgpass" in SENSITIVE_PATTERNS


# ---------------------------------------------------------------------------
# is_sensitive
# ---------------------------------------------------------------------------

class TestIsSensitive:
    def test_dotenv_is_sensitive(self):
        assert is_sensitive("/project/.env") is True

    def test_dotenv_local_is_sensitive(self):
        assert is_sensitive("/project/.env.local") is True

    def test_dotenv_production_is_sensitive(self):
        assert is_sensitive("/project/.env.production") is True

    def test_pem_is_sensitive(self):
        assert is_sensitive("/certs/server.pem") is True

    def test_key_is_sensitive(self):
        assert is_sensitive("/certs/server.key") is True

    def test_p12_is_sensitive(self):
        assert is_sensitive("/certs/client.p12") is True

    def test_credentials_json_is_sensitive(self):
        assert is_sensitive("/home/user/credentials.json") is True

    def test_secret_file_is_sensitive(self):
        assert is_sensitive("/app/db_secret.txt") is True

    def test_supersecret_is_sensitive(self):
        assert is_sensitive("/app/supersecret") is True

    def test_id_rsa_is_sensitive(self):
        assert is_sensitive("/home/user/.ssh/id_rsa") is True

    def test_id_rsa_pub_is_sensitive(self):
        assert is_sensitive("/home/user/.ssh/id_rsa.pub") is True

    def test_id_ed25519_is_sensitive(self):
        assert is_sensitive("/home/user/.ssh/id_ed25519") is True

    def test_id_ed25519_pub_is_sensitive(self):
        assert is_sensitive("/home/user/.ssh/id_ed25519.pub") is True

    def test_token_json_is_sensitive(self):
        assert is_sensitive("/app/token.json") is True

    def test_keystore_is_sensitive(self):
        assert is_sensitive("/app/release.keystore") is True

    def test_netrc_is_sensitive(self):
        assert is_sensitive("/home/user/.netrc") is True

    def test_pgpass_is_sensitive(self):
        assert is_sensitive("/home/user/.pgpass") is True

    def test_ssh_dir_is_sensitive(self):
        ssh_path = os.path.expanduser("~/.ssh/known_hosts")
        assert is_sensitive(ssh_path) is True

    def test_aws_credentials_is_sensitive(self):
        aws_path = os.path.expanduser("~/.aws/credentials")
        assert is_sensitive(aws_path) is True

    def test_gcloud_config_is_sensitive(self):
        gcloud_path = os.path.expanduser("~/.config/gcloud/application_default_credentials.json")
        assert is_sensitive(gcloud_path) is True

    def test_normal_python_file_is_not_sensitive(self):
        assert is_sensitive("/project/main.py") is False

    def test_readme_is_not_sensitive(self):
        assert is_sensitive("/project/README.md") is False

    def test_config_yaml_is_not_sensitive(self):
        assert is_sensitive("/project/config.yaml") is False

    def test_requirements_is_not_sensitive(self):
        assert is_sensitive("/project/requirements.txt") is False


# ---------------------------------------------------------------------------
# check_write
# ---------------------------------------------------------------------------

class TestCheckWrite:
    # --- blocked files ---

    def test_dotenv_is_blocked(self):
        r = check_write("/project/.env")
        assert r.allowed is False
        assert r.severity == "block"

    def test_dotenv_local_is_blocked(self):
        r = check_write("/project/.env.local")
        assert r.allowed is False
        assert r.severity == "block"

    def test_pem_is_blocked(self):
        r = check_write("/certs/server.pem")
        assert r.allowed is False
        assert r.severity == "block"

    def test_key_is_blocked(self):
        r = check_write("/certs/server.key")
        assert r.allowed is False
        assert r.severity == "block"

    def test_p12_is_blocked(self):
        r = check_write("/certs/client.p12")
        assert r.allowed is False
        assert r.severity == "block"

    def test_id_rsa_is_blocked(self):
        r = check_write("/home/user/.ssh/id_rsa")
        assert r.allowed is False
        assert r.severity == "block"

    def test_id_rsa_pub_is_blocked(self):
        r = check_write("/home/user/.ssh/id_rsa.pub")
        assert r.allowed is False
        assert r.severity == "block"

    def test_id_ed25519_is_blocked(self):
        r = check_write("/home/user/.ssh/id_ed25519")
        assert r.allowed is False
        assert r.severity == "block"

    def test_netrc_is_blocked(self):
        r = check_write("/home/user/.netrc")
        assert r.allowed is False
        assert r.severity == "block"

    def test_pgpass_is_blocked(self):
        r = check_write("/home/user/.pgpass")
        assert r.allowed is False
        assert r.severity == "block"

    def test_keystore_is_blocked(self):
        r = check_write("/app/release.keystore")
        assert r.allowed is False
        assert r.severity == "block"

    def test_ssh_dir_path_is_blocked(self):
        ssh_key = os.path.expanduser("~/.ssh/id_rsa")
        r = check_write(ssh_key)
        assert r.allowed is False
        assert r.severity == "block"

    def test_aws_dir_path_is_blocked(self):
        aws_creds = os.path.expanduser("~/.aws/credentials")
        r = check_write(aws_creds)
        assert r.allowed is False
        assert r.severity == "block"

    # --- warned files ---

    def test_credentials_json_is_warned(self):
        r = check_write("/home/user/credentials.json")
        assert r.allowed is True
        assert r.severity == "warn"

    def test_secret_file_is_warned(self):
        r = check_write("/app/db_secret.txt")
        assert r.allowed is True
        assert r.severity == "warn"

    def test_token_json_is_warned(self):
        r = check_write("/app/token.json")
        assert r.allowed is True
        assert r.severity == "warn"

    # --- allowed files ---

    def test_python_file_is_allowed(self):
        r = check_write("/project/main.py")
        assert r.allowed is True
        assert r.severity == "allow"

    def test_readme_is_allowed(self):
        r = check_write("/project/README.md")
        assert r.allowed is True
        assert r.severity == "allow"

    # --- reason text ---

    def test_block_has_reason(self):
        r = check_write("/project/.env")
        assert len(r.reason) > 0

    def test_warn_has_reason(self):
        r = check_write("/project/credentials.json")
        assert len(r.reason) > 0

    def test_allow_has_empty_reason(self):
        r = check_write("/project/main.py")
        assert r.reason == ""


# ---------------------------------------------------------------------------
# check_read
# ---------------------------------------------------------------------------

class TestCheckRead:
    def test_dotenv_read_is_warned(self):
        r = check_read("/project/.env")
        assert r.allowed is True
        assert r.severity == "warn"

    def test_pem_read_is_warned(self):
        r = check_read("/certs/server.pem")
        assert r.allowed is True
        assert r.severity == "warn"

    def test_id_rsa_read_is_warned(self):
        r = check_read("/home/user/.ssh/id_rsa")
        assert r.allowed is True
        assert r.severity == "warn"

    def test_aws_creds_read_is_warned(self):
        aws_creds = os.path.expanduser("~/.aws/credentials")
        r = check_read(aws_creds)
        assert r.allowed is True
        assert r.severity == "warn"

    def test_warn_has_reason_mentioning_llm(self):
        r = check_read("/project/.env")
        assert "LLM" in r.reason or "sensitive" in r.reason.lower()

    def test_normal_file_read_is_allowed(self):
        r = check_read("/project/main.py")
        assert r.allowed is True
        assert r.severity == "allow"

    def test_normal_file_read_has_empty_reason(self):
        r = check_read("/project/main.py")
        assert r.reason == ""
