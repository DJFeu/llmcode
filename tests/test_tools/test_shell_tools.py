"""Tests for llm_code.tools.bash, glob_search, grep_search — TDD."""
from __future__ import annotations

import time


from llm_code.tools.base import PermissionLevel
from llm_code.tools.bash import BashTool
from llm_code.tools.glob_search import GlobSearchTool
from llm_code.tools.grep_search import GrepSearchTool


# ---------------------------------------------------------------------------
# BashTool
# ---------------------------------------------------------------------------

class TestBashTool:
    def test_name(self):
        assert BashTool().name == "bash"

    def test_permission(self):
        assert BashTool().required_permission == PermissionLevel.FULL_ACCESS

    def test_runs_simple_command(self):
        result = BashTool().execute({"command": "echo hello"})
        assert result.is_error is False
        assert "hello" in result.output

    def test_captures_stderr(self):
        result = BashTool().execute({"command": "echo err >&2"})
        # stderr may appear in output or be silent, not an error
        assert result.is_error is False

    def test_exit_nonzero_is_error(self):
        result = BashTool().execute({"command": "exit 1"})
        assert result.is_error is True

    def test_output_truncated_when_large(self):
        # Generate >8000 bytes of output
        result = BashTool(max_output=100).execute({"command": "python3 -c \"print('x'*200)\""})
        assert len(result.output) <= 200  # truncated + notice
        assert result.is_error is False

    def test_timeout_returns_error(self):
        result = BashTool(default_timeout=1).execute({"command": "sleep 5", "timeout": 1})
        assert result.is_error is True
        assert "timeout" in result.output.lower() or "timed" in result.output.lower()

    def test_dangerous_pattern_detected(self):
        result = BashTool().execute({"command": "rm -rf /"})
        assert result.is_error is True
        assert result.metadata is not None
        assert result.metadata.get("dangerous") is True

    def test_dangerous_pattern_rm_rf_home(self):
        result = BashTool().execute({"command": "rm -rf ~"})
        assert result.is_error is True
        assert result.metadata is not None
        assert result.metadata.get("dangerous") is True

    def test_custom_timeout_arg(self):
        result = BashTool().execute({"command": "echo hi", "timeout": 10})
        assert "hi" in result.output

    def test_multiline_output(self):
        result = BashTool().execute({"command": "printf 'a\\nb\\nc'"})
        assert "a" in result.output
        assert "b" in result.output

    def test_to_definition_has_schema(self):
        defn = BashTool().to_definition()
        assert "command" in defn.input_schema.get("properties", {})


# ---------------------------------------------------------------------------
# GlobSearchTool
# ---------------------------------------------------------------------------

class TestGlobSearchTool:
    def test_name(self):
        assert GlobSearchTool().name == "glob_search"

    def test_permission(self):
        assert GlobSearchTool().required_permission == PermissionLevel.READ_ONLY

    def test_finds_matching_files(self, tmp_path):
        (tmp_path / "a.py").write_text("")
        (tmp_path / "b.py").write_text("")
        (tmp_path / "c.txt").write_text("")
        result = GlobSearchTool().execute({"pattern": "*.py", "path": str(tmp_path)})
        assert result.is_error is False
        assert "a.py" in result.output
        assert "b.py" in result.output
        assert "c.txt" not in result.output

    def test_no_matches_returns_empty_message(self, tmp_path):
        result = GlobSearchTool().execute({"pattern": "*.xyz", "path": str(tmp_path)})
        assert result.is_error is False

    def test_recursive_glob(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "deep.py").write_text("")
        result = GlobSearchTool().execute({"pattern": "**/*.py", "path": str(tmp_path)})
        assert "deep.py" in result.output

    def test_sorted_by_mtime(self, tmp_path):
        older = tmp_path / "older.py"
        newer = tmp_path / "newer.py"
        older.write_text("")
        time.sleep(0.01)
        newer.write_text("")
        result = GlobSearchTool().execute({"pattern": "*.py", "path": str(tmp_path)})
        lines = [ln for ln in result.output.splitlines() if ln.strip()]
        # newer should appear before older (sorted by mtime descending)
        assert lines.index(str(newer)) < lines.index(str(older))

    def test_max_100_results(self, tmp_path):
        for i in range(120):
            (tmp_path / f"f{i}.py").write_text("")
        result = GlobSearchTool().execute({"pattern": "*.py", "path": str(tmp_path)})
        lines = [ln for ln in result.output.splitlines() if ln.strip()]
        assert len(lines) <= 100

    def test_to_definition_has_schema(self):
        defn = GlobSearchTool().to_definition()
        assert "pattern" in defn.input_schema.get("properties", {})


# ---------------------------------------------------------------------------
# GrepSearchTool
# ---------------------------------------------------------------------------

class TestGrepSearchTool:
    def test_name(self):
        assert GrepSearchTool().name == "grep_search"

    def test_permission(self):
        assert GrepSearchTool().required_permission == PermissionLevel.READ_ONLY

    def test_finds_matches(self, tmp_path):
        f = tmp_path / "code.py"
        f.write_text("def foo():\n    pass\ndef bar():\n    pass\n")
        result = GrepSearchTool().execute({"pattern": "def ", "path": str(tmp_path)})
        assert result.is_error is False
        assert "foo" in result.output
        assert "bar" in result.output

    def test_no_matches(self, tmp_path):
        f = tmp_path / "code.py"
        f.write_text("hello world\n")
        result = GrepSearchTool().execute({"pattern": "NOTFOUND", "path": str(tmp_path)})
        assert result.is_error is False

    def test_glob_filter(self, tmp_path):
        (tmp_path / "code.py").write_text("target\n")
        (tmp_path / "notes.txt").write_text("target\n")
        result = GrepSearchTool().execute({
            "pattern": "target",
            "path": str(tmp_path),
            "glob": "*.py",
        })
        assert "code.py" in result.output
        assert "notes.txt" not in result.output

    def test_context_lines(self, tmp_path):
        f = tmp_path / "code.py"
        f.write_text("before\ntarget\nafter\n")
        result = GrepSearchTool().execute({
            "pattern": "target",
            "path": str(tmp_path),
            "context": 1,
        })
        assert "before" in result.output
        assert "after" in result.output

    def test_regex_pattern(self, tmp_path):
        f = tmp_path / "code.py"
        f.write_text("foo123\nbar456\n")
        result = GrepSearchTool().execute({"pattern": r"\d+", "path": str(tmp_path)})
        assert "foo123" in result.output
        assert "bar456" in result.output

    def test_to_definition_has_schema(self):
        defn = GrepSearchTool().to_definition()
        props = defn.input_schema.get("properties", {})
        assert "pattern" in props
        assert "path" in props
