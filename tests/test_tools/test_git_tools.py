"""Tests for llm_code.tools.git_tools — TDD (RED first)."""
from __future__ import annotations

import subprocess

import pytest

from llm_code.tools.base import PermissionLevel
from llm_code.tools.git_tools import (
    GitBranchTool,
    GitCommitTool,
    GitDiffTool,
    GitLogTool,
    GitPushTool,
    GitStashTool,
    GitStatusTool,
)


# ---------------------------------------------------------------------------
# Shared git repo fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def git_repo(tmp_path):
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=tmp_path, capture_output=True)
    (tmp_path / "main.py").write_text("print('hello')")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, capture_output=True)
    return tmp_path


# ---------------------------------------------------------------------------
# GitStatusTool
# ---------------------------------------------------------------------------


class TestGitStatusTool:
    def test_name(self):
        assert GitStatusTool().name == "git_status"

    def test_permission(self):
        assert GitStatusTool().required_permission == PermissionLevel.READ_ONLY

    def test_is_read_only(self):
        assert GitStatusTool().is_read_only({}) is True

    def test_is_concurrency_safe(self):
        assert GitStatusTool().is_concurrency_safe({}) is True

    def test_is_not_destructive(self):
        assert GitStatusTool().is_destructive({}) is False

    def test_clean_repo(self, git_repo, monkeypatch):
        monkeypatch.chdir(git_repo)
        result = GitStatusTool().execute({})
        assert result.is_error is False
        # Clean repo — output is empty or whitespace
        assert result.output.strip() == ""

    def test_modified_file(self, git_repo, monkeypatch):
        monkeypatch.chdir(git_repo)
        (git_repo / "main.py").write_text("print('changed')")
        result = GitStatusTool().execute({})
        assert result.is_error is False
        assert "main.py" in result.output


# ---------------------------------------------------------------------------
# GitDiffTool
# ---------------------------------------------------------------------------


class TestGitDiffTool:
    def test_name(self):
        assert GitDiffTool().name == "git_diff"

    def test_permission(self):
        assert GitDiffTool().required_permission == PermissionLevel.READ_ONLY

    def test_is_read_only(self):
        assert GitDiffTool().is_read_only({}) is True

    def test_is_concurrency_safe(self):
        assert GitDiffTool().is_concurrency_safe({}) is True

    def test_no_changes(self, git_repo, monkeypatch):
        monkeypatch.chdir(git_repo)
        result = GitDiffTool().execute({})
        assert result.is_error is False
        assert result.output.strip() == ""

    def test_with_changes(self, git_repo, monkeypatch):
        monkeypatch.chdir(git_repo)
        (git_repo / "main.py").write_text("print('changed')")
        result = GitDiffTool().execute({})
        assert result.is_error is False
        assert "main.py" in result.output

    def test_staged_diff(self, git_repo, monkeypatch):
        monkeypatch.chdir(git_repo)
        (git_repo / "main.py").write_text("print('staged')")
        subprocess.run(["git", "add", "main.py"], cwd=git_repo, capture_output=True)
        result = GitDiffTool().execute({"staged": True})
        assert result.is_error is False
        assert "main.py" in result.output

    def test_unstaged_does_not_show_staged(self, git_repo, monkeypatch):
        monkeypatch.chdir(git_repo)
        (git_repo / "main.py").write_text("print('staged')")
        subprocess.run(["git", "add", "main.py"], cwd=git_repo, capture_output=True)
        result = GitDiffTool().execute({"staged": False})
        # Unstaged diff should be empty since change is staged
        assert result.is_error is False
        assert result.output.strip() == ""


# ---------------------------------------------------------------------------
# GitLogTool
# ---------------------------------------------------------------------------


class TestGitLogTool:
    def test_name(self):
        assert GitLogTool().name == "git_log"

    def test_permission(self):
        assert GitLogTool().required_permission == PermissionLevel.READ_ONLY

    def test_is_read_only(self):
        assert GitLogTool().is_read_only({}) is True

    def test_is_concurrency_safe(self):
        assert GitLogTool().is_concurrency_safe({}) is True

    def test_default_log(self, git_repo, monkeypatch):
        monkeypatch.chdir(git_repo)
        result = GitLogTool().execute({})
        assert result.is_error is False
        assert "init" in result.output

    def test_custom_limit(self, git_repo, monkeypatch):
        monkeypatch.chdir(git_repo)
        # Add a second commit
        (git_repo / "foo.py").write_text("x = 1")
        subprocess.run(["git", "add", "-A"], cwd=git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "second"], cwd=git_repo, capture_output=True)
        result = GitLogTool().execute({"limit": 1})
        assert result.is_error is False
        lines = [ln for ln in result.output.splitlines() if ln.strip()]
        assert len(lines) == 1

    def test_oneline_format(self, git_repo, monkeypatch):
        monkeypatch.chdir(git_repo)
        result = GitLogTool().execute({"oneline": True})
        assert result.is_error is False
        # Each line contains the short hash + message
        for line in result.output.splitlines():
            if line.strip():
                parts = line.strip().split(" ", 1)
                assert len(parts) == 2  # hash + message


# ---------------------------------------------------------------------------
# GitCommitTool
# ---------------------------------------------------------------------------


class TestGitCommitTool:
    def test_name(self):
        assert GitCommitTool().name == "git_commit"

    def test_permission(self):
        assert GitCommitTool().required_permission == PermissionLevel.WORKSPACE_WRITE

    def test_is_not_read_only(self):
        assert GitCommitTool().is_read_only({}) is False

    def test_is_not_destructive(self):
        assert GitCommitTool().is_destructive({}) is False

    def test_basic_commit(self, git_repo, monkeypatch):
        monkeypatch.chdir(git_repo)
        (git_repo / "new.py").write_text("x = 42")
        result = GitCommitTool().execute({"message": "add new.py"})
        assert result.is_error is False
        assert "new.py" in result.output or "add new.py" in result.output

    def test_commit_with_specific_files(self, git_repo, monkeypatch):
        monkeypatch.chdir(git_repo)
        (git_repo / "a.py").write_text("a = 1")
        (git_repo / "b.py").write_text("b = 2")
        result = GitCommitTool().execute({"message": "only a", "files": ["a.py"]})
        assert result.is_error is False
        # b.py should remain untracked
        status = subprocess.run(
            ["git", "status", "--short"], cwd=git_repo, capture_output=True, text=True
        )
        assert "b.py" in status.stdout

    def test_sensitive_file_blocked(self, git_repo, monkeypatch):
        monkeypatch.chdir(git_repo)
        (git_repo / ".env").write_text("SECRET=abc")
        result = GitCommitTool().execute({"message": "oops", "files": [".env"]})
        assert result.is_error is True
        assert "sensitive" in result.output.lower() or ".env" in result.output

    def test_sensitive_key_file_blocked(self, git_repo, monkeypatch):
        monkeypatch.chdir(git_repo)
        (git_repo / "id_rsa.key").write_text("-----BEGIN RSA PRIVATE KEY-----")
        result = GitCommitTool().execute({"message": "oops", "files": ["id_rsa.key"]})
        assert result.is_error is True

    def test_sensitive_credentials_file_blocked(self, git_repo, monkeypatch):
        monkeypatch.chdir(git_repo)
        (git_repo / "credentials.json").write_text("{}")
        result = GitCommitTool().execute({"message": "oops", "files": ["credentials.json"]})
        assert result.is_error is True

    def test_no_changes_returns_error(self, git_repo, monkeypatch):
        monkeypatch.chdir(git_repo)
        result = GitCommitTool().execute({"message": "nothing"})
        assert result.is_error is True


# ---------------------------------------------------------------------------
# GitPushTool
# ---------------------------------------------------------------------------


class TestGitPushTool:
    def test_name(self):
        assert GitPushTool().name == "git_push"

    def test_permission(self):
        assert GitPushTool().required_permission == PermissionLevel.FULL_ACCESS

    def test_is_destructive(self):
        assert GitPushTool().is_destructive({}) is True

    def test_is_not_read_only(self):
        assert GitPushTool().is_read_only({}) is False

    def test_push_fails_without_remote(self, git_repo, monkeypatch):
        monkeypatch.chdir(git_repo)
        result = GitPushTool().execute({"remote": "origin", "branch": "main"})
        # No remote configured — should fail
        assert result.is_error is True

    def test_push_default_remote(self, git_repo, monkeypatch):
        monkeypatch.chdir(git_repo)
        result = GitPushTool().execute({})
        # No remote — error, but tool should still run (not raise exception)
        assert isinstance(result.is_error, bool)


# ---------------------------------------------------------------------------
# GitStashTool
# ---------------------------------------------------------------------------


class TestGitStashTool:
    def test_name(self):
        assert GitStashTool().name == "git_stash"

    def test_permission(self):
        assert GitStashTool().required_permission == PermissionLevel.WORKSPACE_WRITE

    def test_stash_list_empty(self, git_repo, monkeypatch):
        monkeypatch.chdir(git_repo)
        result = GitStashTool().execute({"action": "list"})
        assert result.is_error is False
        assert result.output.strip() == ""

    def test_stash_push_and_pop(self, git_repo, monkeypatch):
        monkeypatch.chdir(git_repo)
        (git_repo / "main.py").write_text("print('dirty')")
        push_result = GitStashTool().execute({"action": "push", "message": "wip"})
        assert push_result.is_error is False
        # File should be restored to original
        assert (git_repo / "main.py").read_text() == "print('hello')"
        pop_result = GitStashTool().execute({"action": "pop"})
        assert pop_result.is_error is False
        assert (git_repo / "main.py").read_text() == "print('dirty')"

    def test_stash_list_shows_entry(self, git_repo, monkeypatch):
        monkeypatch.chdir(git_repo)
        (git_repo / "main.py").write_text("print('dirty')")
        GitStashTool().execute({"action": "push", "message": "wip"})
        result = GitStashTool().execute({"action": "list"})
        assert result.is_error is False
        assert "wip" in result.output


# ---------------------------------------------------------------------------
# GitBranchTool
# ---------------------------------------------------------------------------


class TestGitBranchTool:
    def test_name(self):
        assert GitBranchTool().name == "git_branch"

    def test_permission(self):
        assert GitBranchTool().required_permission == PermissionLevel.WORKSPACE_WRITE

    def test_is_destructive_delete(self):
        assert GitBranchTool().is_destructive({"action": "delete"}) is True

    def test_is_not_destructive_list(self):
        assert GitBranchTool().is_destructive({"action": "list"}) is False

    def test_is_not_destructive_create(self):
        assert GitBranchTool().is_destructive({"action": "create"}) is False

    def test_branch_list(self, git_repo, monkeypatch):
        monkeypatch.chdir(git_repo)
        result = GitBranchTool().execute({"action": "list"})
        assert result.is_error is False
        # Should list at least one branch
        assert result.output.strip() != ""

    def test_branch_create(self, git_repo, monkeypatch):
        monkeypatch.chdir(git_repo)
        result = GitBranchTool().execute({"action": "create", "name": "feature-x"})
        assert result.is_error is False
        # Verify branch exists
        branches = subprocess.run(
            ["git", "branch"], cwd=git_repo, capture_output=True, text=True
        )
        assert "feature-x" in branches.stdout

    def test_branch_switch(self, git_repo, monkeypatch):
        monkeypatch.chdir(git_repo)
        subprocess.run(["git", "branch", "feat"], cwd=git_repo, capture_output=True)
        result = GitBranchTool().execute({"action": "switch", "name": "feat"})
        assert result.is_error is False

    def test_branch_delete(self, git_repo, monkeypatch):
        monkeypatch.chdir(git_repo)
        subprocess.run(["git", "branch", "to-delete"], cwd=git_repo, capture_output=True)
        result = GitBranchTool().execute({"action": "delete", "name": "to-delete"})
        assert result.is_error is False
        branches = subprocess.run(
            ["git", "branch"], cwd=git_repo, capture_output=True, text=True
        )
        assert "to-delete" not in branches.stdout
