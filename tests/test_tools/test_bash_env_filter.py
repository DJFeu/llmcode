"""Tests for environment variable filtering in BashTool."""
from __future__ import annotations

from unittest.mock import patch


from llm_code.tools.bash import _make_safe_env


class TestMakeSafeEnv:
    def test_path_preserved(self) -> None:
        with patch.dict("os.environ", {"PATH": "/usr/bin"}, clear=True):
            env = _make_safe_env()
            assert env["PATH"] == "/usr/bin"

    def test_home_preserved(self) -> None:
        with patch.dict("os.environ", {"HOME": "/home/user"}, clear=True):
            env = _make_safe_env()
            assert env["HOME"] == "/home/user"

    def test_openai_api_key_filtered(self) -> None:
        with patch.dict(
            "os.environ",
            {"OPENAI_API_KEY": "sk-abc123", "PATH": "/usr/bin"},
            clear=True,
        ):
            env = _make_safe_env()
            assert env["OPENAI_API_KEY"] == "[FILTERED]"

    def test_my_secret_token_filtered(self) -> None:
        with patch.dict(
            "os.environ",
            {"MY_SECRET_TOKEN": "supersecret"},
            clear=True,
        ):
            env = _make_safe_env()
            assert env["MY_SECRET_TOKEN"] == "[FILTERED]"

    def test_aws_access_key_id_filtered(self) -> None:
        with patch.dict(
            "os.environ",
            {"AWS_ACCESS_KEY_ID": "AKIA1234"},
            clear=True,
        ):
            env = _make_safe_env()
            assert env["AWS_ACCESS_KEY_ID"] == "[FILTERED]"

    def test_custom_variable_preserved(self) -> None:
        with patch.dict(
            "os.environ",
            {"CUSTOM_VARIABLE": "hello"},
            clear=True,
        ):
            env = _make_safe_env()
            assert env["CUSTOM_VARIABLE"] == "hello"

    def test_ssh_auth_sock_in_allowlist(self) -> None:
        with patch.dict(
            "os.environ",
            {"SSH_AUTH_SOCK": "/tmp/agent.sock"},
            clear=True,
        ):
            env = _make_safe_env()
            assert env["SSH_AUTH_SOCK"] == "/tmp/agent.sock"

    def test_empty_env_produces_empty(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            env = _make_safe_env()
            assert env == {}

    def test_password_variable_filtered(self) -> None:
        with patch.dict(
            "os.environ",
            {"DB_PASSWORD": "secret123"},
            clear=True,
        ):
            env = _make_safe_env()
            assert env["DB_PASSWORD"] == "[FILTERED]"

    def test_auth_header_filtered(self) -> None:
        with patch.dict(
            "os.environ",
            {"AUTH_BEARER": "token123"},
            clear=True,
        ):
            env = _make_safe_env()
            assert env["AUTH_BEARER"] == "[FILTERED]"
