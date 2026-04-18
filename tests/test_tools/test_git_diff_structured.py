"""M5: structured git diff + auto-commit helper."""
from __future__ import annotations


class TestFileDiff:
    def test_parse_unified_diff(self) -> None:
        from llm_code.tools.git_diff_structured import parse_unified_diff

        diff_text = (
            "diff --git a/x.py b/x.py\n"
            "--- a/x.py\n"
            "+++ b/x.py\n"
            "@@ -1,2 +1,2 @@\n"
            "-old line\n"
            "+new line\n"
            " unchanged\n"
        )
        files = parse_unified_diff(diff_text)
        assert len(files) == 1
        fd = files[0]
        assert fd.path == "x.py"
        assert fd.additions == 1
        assert fd.deletions == 1

    def test_multi_file_diff(self) -> None:
        from llm_code.tools.git_diff_structured import parse_unified_diff

        text = (
            "diff --git a/a b/a\n--- a/a\n+++ b/a\n@@\n+added\n"
            "diff --git a/b b/b\n--- a/b\n+++ b/b\n@@\n-removed\n"
        )
        files = parse_unified_diff(text)
        paths = [f.path for f in files]
        assert paths == ["a", "b"]
        assert files[0].additions == 1
        assert files[1].deletions == 1

    def test_empty_diff(self) -> None:
        from llm_code.tools.git_diff_structured import parse_unified_diff
        assert parse_unified_diff("") == ()


class TestAutoCommitMessage:
    def test_generate_message_from_last_assistant(self) -> None:
        from llm_code.tools.git_diff_structured import (
            build_auto_commit_message,
        )
        msg = build_auto_commit_message(
            last_assistant_text="Added input validation to the login flow.",
            files_changed=["src/auth.py", "tests/test_auth.py"],
        )
        assert msg.startswith("auto:")
        assert "login flow" in msg or "Added input validation" in msg

    def test_fallback_when_no_text(self) -> None:
        from llm_code.tools.git_diff_structured import (
            build_auto_commit_message,
        )
        msg = build_auto_commit_message(
            last_assistant_text="",
            files_changed=["a.py", "b.py"],
        )
        # Falls back to file-based summary.
        assert "2 file" in msg or "a.py" in msg
