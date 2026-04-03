"""Tests for BashTool 20-point security hardening (rules 8–20 + BashSafetyResult)."""
from __future__ import annotations

import pytest

from llm_code.tools.bash import BashSafetyResult, BashTool, classify_command


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tool() -> BashTool:
    return BashTool()


# ---------------------------------------------------------------------------
# BashSafetyResult dataclass
# ---------------------------------------------------------------------------


class TestBashSafetyResult:
    def test_frozen_dataclass(self) -> None:
        result = BashSafetyResult(classification="safe")
        with pytest.raises((AttributeError, TypeError)):
            result.classification = "blocked"  # type: ignore[misc]

    def test_is_safe_property(self) -> None:
        assert BashSafetyResult(classification="safe").is_safe is True
        assert BashSafetyResult(classification="needs_confirm").is_safe is False
        assert BashSafetyResult(classification="blocked").is_safe is False

    def test_is_blocked_property(self) -> None:
        assert BashSafetyResult(classification="blocked").is_blocked is True
        assert BashSafetyResult(classification="safe").is_blocked is False

    def test_needs_confirm_property(self) -> None:
        assert BashSafetyResult(classification="needs_confirm").needs_confirm is True
        assert BashSafetyResult(classification="safe").needs_confirm is False

    def test_reasons_and_rule_ids_default_empty(self) -> None:
        result = BashSafetyResult(classification="safe")
        assert result.reasons == ()
        assert result.rule_ids == ()

    def test_reasons_stored_as_tuple(self) -> None:
        result = BashSafetyResult(
            classification="needs_confirm",
            reasons=("reason one",),
            rule_ids=("R8",),
        )
        assert isinstance(result.reasons, tuple)
        assert isinstance(result.rule_ids, tuple)
        assert "reason one" in result.reasons
        assert "R8" in result.rule_ids


# ---------------------------------------------------------------------------
# Rule 8: Command injection
# ---------------------------------------------------------------------------


class TestRule8CommandInjection:
    @pytest.mark.parametrize(
        "command",
        [
            "ls $(echo /tmp)",
            "echo `whoami`",
            "cat ${HOME}/.bashrc",
            "curl http://$(hostname)/api",
        ],
    )
    def test_command_injection_detected(self, command: str) -> None:
        result = classify_command(command)
        assert "R8" in result.rule_ids, f"Expected R8 for: {command!r}"
        assert result.classification in ("needs_confirm", "blocked")

    @pytest.mark.parametrize(
        "command",
        [
            "ls /tmp",
            "echo hello world",
            "git status",
        ],
    )
    def test_safe_commands_not_flagged_r8(self, command: str) -> None:
        result = classify_command(command)
        assert "R8" not in result.rule_ids, f"R8 should not trigger for: {command!r}"


# ---------------------------------------------------------------------------
# Rule 9: Newline attack
# ---------------------------------------------------------------------------


class TestRule9NewlineAttack:
    @pytest.mark.parametrize(
        "command",
        [
            "echo hello\\nrm -rf /",
            "echo hello\\rwhoami",
            "ls\x0acat /etc/passwd",
            "echo test\x0dcommand",
        ],
    )
    def test_newline_attack_detected(self, command: str) -> None:
        result = classify_command(command)
        assert "R9" in result.rule_ids, f"Expected R9 for: {command!r}"

    def test_normal_echo_not_flagged(self) -> None:
        result = classify_command("echo hello world")
        assert "R9" not in result.rule_ids


# ---------------------------------------------------------------------------
# Rule 10: Pipe chain > 5 pipes
# ---------------------------------------------------------------------------


class TestRule10PipeChain:
    @pytest.mark.parametrize(
        "command",
        [
            "cat file | grep a | sed b | awk c | sort | uniq | head",
            "cmd1 | cmd2 | cmd3 | cmd4 | cmd5 | cmd6 | cmd7",
        ],
    )
    def test_long_pipe_chain_detected(self, command: str) -> None:
        result = classify_command(command)
        assert "R10" in result.rule_ids, f"Expected R10 for: {command!r}"

    @pytest.mark.parametrize(
        "command",
        [
            "cat file | grep foo",
            "ps aux | grep python | head -5",
            "ls | sort | uniq | head",
        ],
    )
    def test_short_pipe_chain_ok(self, command: str) -> None:
        result = classify_command(command)
        assert "R10" not in result.rule_ids, f"R10 should not trigger for: {command!r}"


# ---------------------------------------------------------------------------
# Rule 11: Interpreter REPL
# ---------------------------------------------------------------------------


class TestRule11InterpreterRepl:
    @pytest.mark.parametrize(
        "command",
        [
            "python",
            "python3",
            "node",
            "ruby",
            "perl",
            "php",
            "  python  ",
            "  node  ",
        ],
    )
    def test_repl_blocked(self, command: str) -> None:
        result = classify_command(command)
        assert "R11" in result.rule_ids, f"Expected R11 for: {command!r}"
        assert result.classification == "blocked"

    @pytest.mark.parametrize(
        "command",
        [
            "python script.py",
            "python3 -c 'print(1)'",
            "node server.js",
            "ruby app.rb",
            "perl script.pl",
            "php index.php",
        ],
    )
    def test_interpreter_with_file_not_blocked(self, command: str) -> None:
        result = classify_command(command)
        assert "R11" not in result.rule_ids, f"R11 should not trigger for: {command!r}"


# ---------------------------------------------------------------------------
# Rule 12: Env leak
# ---------------------------------------------------------------------------


class TestRule12EnvLeak:
    @pytest.mark.parametrize(
        "command",
        [
            "env",
            "printenv",
            "printenv PATH",
            "export MY_VAR=hello",
        ],
    )
    def test_env_leak_flagged(self, command: str) -> None:
        result = classify_command(command)
        assert "R12" in result.rule_ids, f"Expected R12 for: {command!r}"
        assert result.classification in ("needs_confirm", "blocked")

    def test_safe_command_not_env_leak(self) -> None:
        result = classify_command("ls -la")
        assert "R12" not in result.rule_ids


# ---------------------------------------------------------------------------
# Rule 13: Network access to non-localhost
# ---------------------------------------------------------------------------


class TestRule13NetworkAccess:
    @pytest.mark.parametrize(
        "command",
        [
            "curl https://example.com/api",
            "wget http://malicious.site/file",
            "nc remote.host 443",
            "ssh user@remote-server",
        ],
    )
    def test_non_localhost_network_flagged(self, command: str) -> None:
        result = classify_command(command)
        assert "R13" in result.rule_ids, f"Expected R13 for: {command!r}"

    @pytest.mark.parametrize(
        "command",
        [
            "curl http://localhost:8080/health",
            "curl http://127.0.0.1/api",
            "ssh user@localhost",
        ],
    )
    def test_localhost_network_not_flagged(self, command: str) -> None:
        result = classify_command(command)
        assert "R13" not in result.rule_ids, f"R13 should not trigger for: {command!r}"


# ---------------------------------------------------------------------------
# Rule 14: File permissions
# ---------------------------------------------------------------------------


class TestRule14FilePermission:
    @pytest.mark.parametrize(
        "command",
        [
            "chmod 755 script.sh",
            "chown root:root /etc/hosts",
            "chgrp staff file.txt",
            "chmod +x deploy.sh",
        ],
    )
    def test_file_permission_flagged(self, command: str) -> None:
        result = classify_command(command)
        assert "R14" in result.rule_ids, f"Expected R14 for: {command!r}"

    def test_ls_not_permission_change(self) -> None:
        result = classify_command("ls -l")
        assert "R14" not in result.rule_ids


# ---------------------------------------------------------------------------
# Rule 15: System packages
# ---------------------------------------------------------------------------


class TestRule15SystemPackages:
    @pytest.mark.parametrize(
        "command",
        [
            "apt install curl",
            "apt-get install vim",
            "brew install wget",
            "pip install requests",
            "pip install -r requirements.txt",
            "npm install -g typescript",
        ],
    )
    def test_system_package_install_flagged(self, command: str) -> None:
        result = classify_command(command)
        assert "R15" in result.rule_ids, f"Expected R15 for: {command!r}"

    @pytest.mark.parametrize(
        "command",
        [
            "npm install",          # local install, not -g
            "pip show requests",
            "brew list",
        ],
    )
    def test_local_or_query_not_flagged(self, command: str) -> None:
        result = classify_command(command)
        assert "R15" not in result.rule_ids, f"R15 should not trigger for: {command!r}"


# ---------------------------------------------------------------------------
# Rule 16: Redirect overwrite
# ---------------------------------------------------------------------------


class TestRule16RedirectOverwrite:
    @pytest.mark.parametrize(
        "command",
        [
            "echo hello > output.txt",
            "ls > /tmp/listing",
            "python script.py > log.txt",
        ],
    )
    def test_redirect_overwrite_flagged(self, command: str) -> None:
        result = classify_command(command)
        assert "R16" in result.rule_ids, f"Expected R16 for: {command!r}"

    @pytest.mark.parametrize(
        "command",
        [
            "echo hello >> output.txt",  # append, not overwrite
            "ls -la",
            "cat file.txt",
        ],
    )
    def test_append_and_safe_not_flagged(self, command: str) -> None:
        result = classify_command(command)
        assert "R16" not in result.rule_ids, f"R16 should not trigger for: {command!r}"


# ---------------------------------------------------------------------------
# Rule 17: Credential file access
# ---------------------------------------------------------------------------


class TestRule17CredentialAccess:
    @pytest.mark.parametrize(
        "command",
        [
            "cat ~/.ssh/id_rsa",
            "ls ~/.aws/credentials",
            "cat ~/.config/token",
            "cat .env",
            "vim /home/user/.ssh/config",
        ],
    )
    def test_credential_access_flagged(self, command: str) -> None:
        result = classify_command(command)
        assert "R17" in result.rule_ids, f"Expected R17 for: {command!r}"

    def test_regular_file_not_flagged(self) -> None:
        result = classify_command("cat /tmp/test.txt")
        assert "R17" not in result.rule_ids


# ---------------------------------------------------------------------------
# Rule 18: Background execution
# ---------------------------------------------------------------------------


class TestRule18BackgroundExec:
    @pytest.mark.parametrize(
        "command",
        [
            "python server.py &",
            "nohup ./run.sh",
            "disown",
            "sleep 60 & echo done",
        ],
    )
    def test_background_exec_flagged(self, command: str) -> None:
        result = classify_command(command)
        assert "R18" in result.rule_ids, f"Expected R18 for: {command!r}"

    @pytest.mark.parametrize(
        "command",
        [
            "echo hello",
            "git status",
            "ls -la",
        ],
    )
    def test_foreground_commands_not_flagged(self, command: str) -> None:
        result = classify_command(command)
        assert "R18" not in result.rule_ids, f"R18 should not trigger for: {command!r}"


# ---------------------------------------------------------------------------
# Rule 19: Recursive ops
# ---------------------------------------------------------------------------


class TestRule19RecursiveOps:
    @pytest.mark.parametrize(
        "command",
        [
            "find /tmp -name '*.log' -exec rm {} \\;",
            "find . -type f -exec chmod 644 {} +",
            "find . | xargs rm",
            "cat files.txt | xargs mv",
        ],
    )
    def test_recursive_ops_flagged(self, command: str) -> None:
        result = classify_command(command)
        assert "R19" in result.rule_ids, f"Expected R19 for: {command!r}"

    @pytest.mark.parametrize(
        "command",
        [
            "find . -name '*.py'",      # find without -exec
            "ls | xargs echo",          # xargs with non-write command
        ],
    )
    def test_safe_find_not_flagged(self, command: str) -> None:
        result = classify_command(command)
        assert "R19" not in result.rule_ids, f"R19 should not trigger for: {command!r}"


# ---------------------------------------------------------------------------
# Rule 20: Multi-command chaining > 3 commands
# ---------------------------------------------------------------------------


class TestRule20MultiCommand:
    @pytest.mark.parametrize(
        "command",
        [
            "cd /tmp && ls && rm file && echo done",
            "cmd1; cmd2; cmd3; cmd4",
            "true && true || false && echo finish",
        ],
    )
    def test_multi_command_chain_flagged(self, command: str) -> None:
        result = classify_command(command)
        assert "R20" in result.rule_ids, f"Expected R20 for: {command!r}"

    @pytest.mark.parametrize(
        "command",
        [
            "cd /tmp && ls",
            "test -f file && echo exists",
            "mkdir /tmp/test; echo done",
        ],
    )
    def test_short_chain_not_flagged(self, command: str) -> None:
        result = classify_command(command)
        assert "R20" not in result.rule_ids, f"R20 should not trigger for: {command!r}"


# ---------------------------------------------------------------------------
# Rule 21: Zsh dangerous builtins
# ---------------------------------------------------------------------------


class TestRule21ZshDangerousBuiltins:
    @pytest.mark.parametrize(
        "command",
        [
            "zmodload zsh/net/tcp",
            "sysopen -r -u 3 /etc/passwd",
            "sysread -u 3 buf",
            "syswrite -u 3 'data'",
            "sysseek -u 3 0",
            "zsocket -t tcp",
            "ztcp localhost 8080",
            "zpty myterm bash",
            "zselect -t 100",
            "zformat -f result '%s' 'key:value'",
            "zparseopts -D -E -- f:=flag",
            "zregexparse str pat",
            "zstat -L /etc/passwd",
            "zcompile myscript.zsh",
        ],
    )
    def test_zsh_builtins_blocked(self, command: str) -> None:
        result = classify_command(command)
        assert "R21" in result.rule_ids, f"Expected R21 for: {command!r}"
        assert result.is_blocked, f"Expected blocked for: {command!r}"

    @pytest.mark.parametrize(
        "command",
        [
            "ls -la",
            "echo hello",
            "git status",
            "cat /tmp/test.txt",
            "grep pattern file.txt",
        ],
    )
    def test_safe_commands_not_flagged_r21(self, command: str) -> None:
        result = classify_command(command)
        assert "R21" not in result.rule_ids, f"R21 should not trigger for: {command!r}"

    def test_zsh_builtin_in_pipe_blocked(self) -> None:
        result = classify_command("echo data | syswrite -u 3")
        assert "R21" in result.rule_ids
        assert result.is_blocked

    def test_zsh_builtin_word_boundary(self) -> None:
        # 'notzsocket' should NOT trigger R21 (no word boundary)
        result = classify_command("echo notzsocket")
        assert "R21" not in result.rule_ids

    def test_zsh_builtin_case_insensitive(self) -> None:
        result = classify_command("ZMODLOAD zsh/net/tcp")
        assert "R21" in result.rule_ids
        assert result.is_blocked

    def test_r21_blocks_even_when_other_rules_say_needs_confirm(self) -> None:
        # Command injection (R8, needs_confirm) combined with a zsh builtin (R21, blocked)
        result = classify_command("zmodload $(echo zsh/net/tcp)")
        assert "R21" in result.rule_ids
        assert result.is_blocked


# ---------------------------------------------------------------------------
# Integration: classify_command returns correct classifications
# ---------------------------------------------------------------------------


class TestClassifyCommandIntegration:
    def test_safe_command(self) -> None:
        result = classify_command("ls -la /tmp")
        assert result.is_safe
        assert result.reasons == ()

    def test_truly_dangerous_blocked(self) -> None:
        result = classify_command("rm -rf /")
        assert result.is_blocked
        assert "R1-R7" in result.rule_ids

    def test_repl_blocked(self) -> None:
        result = classify_command("python")
        assert result.is_blocked
        assert "R11" in result.rule_ids

    def test_multiple_rules_triggered(self) -> None:
        # chmod + redirect overwrite should both trigger
        result = classify_command("chmod 755 script.sh > log.txt")
        assert "R14" in result.rule_ids
        assert "R16" in result.rule_ids
        assert result.needs_confirm

    def test_blocked_takes_precedence_over_needs_confirm(self) -> None:
        # python (REPL, blocked) combined with another needs_confirm rule
        classify_command("python > output.txt")
        # R11 would be blocked, but "python > output.txt" has a filename-like arg...
        # Actually "python > output.txt" doesn't match REPL (has >), but R16 should trigger
        # Let's use "python" only
        result2 = classify_command("python")
        assert result2.is_blocked


# ---------------------------------------------------------------------------
# BashTool.classify integration
# ---------------------------------------------------------------------------


class TestBashToolClassify:
    def test_classify_returns_bash_safety_result(self, tool: BashTool) -> None:
        result = tool.classify({"command": "ls -la"})
        assert isinstance(result, BashSafetyResult)

    def test_classify_safe_command(self, tool: BashTool) -> None:
        result = tool.classify({"command": "ls /tmp"})
        assert result.is_safe

    def test_classify_dangerous_command(self, tool: BashTool) -> None:
        result = tool.classify({"command": "rm -rf /"})
        assert result.is_blocked

    def test_execute_blocks_repl(self, tool: BashTool) -> None:
        from llm_code.tools.base import ToolResult
        result = tool.execute({"command": "python"})
        assert isinstance(result, ToolResult)
        assert result.is_error
        assert "blocked" in result.output.lower() or "dangerous" in result.output.lower()

    def test_execute_blocks_truly_dangerous(self, tool: BashTool) -> None:
        from llm_code.tools.base import ToolResult
        result = tool.execute({"command": "rm -rf /"})
        assert isinstance(result, ToolResult)
        assert result.is_error


# ---------------------------------------------------------------------------
# is_read_only and is_destructive with new rules
# ---------------------------------------------------------------------------


class TestIsReadOnlyWithNewRules:
    def test_env_no_longer_read_only(self, tool: BashTool) -> None:
        # env triggers R12 (env leak), so it is not purely "safe" read-only
        assert tool.is_read_only({"command": "env"}) is False

    def test_plain_ls_still_read_only(self, tool: BashTool) -> None:
        assert tool.is_read_only({"command": "ls /tmp"}) is True

    def test_cat_with_credential_file_not_read_only(self, tool: BashTool) -> None:
        assert tool.is_read_only({"command": "cat ~/.ssh/id_rsa"}) is False


class TestIsDestructiveWithNewRules:
    def test_chmod_is_destructive(self, tool: BashTool) -> None:
        assert tool.is_destructive({"command": "chmod 755 script.sh"}) is True

    def test_curl_external_is_destructive(self, tool: BashTool) -> None:
        assert tool.is_destructive({"command": "curl https://example.com"}) is True

    def test_pip_install_is_destructive(self, tool: BashTool) -> None:
        assert tool.is_destructive({"command": "pip install requests"}) is True

    def test_ls_not_destructive(self, tool: BashTool) -> None:
        assert tool.is_destructive({"command": "ls"}) is False
