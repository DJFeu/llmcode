"""Tests for llm_code.analysis.engine and llm_code.analysis.cache."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch


from llm_code.analysis.cache import load_results, save_results
from llm_code.analysis.engine import (
    _discover_files,
    _language_for_file,
    run_analysis,
    run_diff_check,
)
from llm_code.analysis.rules import Violation


# ---------------------------------------------------------------------------
# Cache tests
# ---------------------------------------------------------------------------

class TestCacheSaveLoad:
    def test_round_trip(self, tmp_path: Path) -> None:
        violations = (
            Violation(
                rule_key="bare-except",
                severity="high",
                file_path="src/api.py",
                line=10,
                message="Bare except clause",
            ),
            Violation(
                rule_key="todo-fixme",
                severity="low",
                file_path="src/api.py",
                line=5,
                message="TODO comment found",
            ),
        )
        cache_path = save_results(tmp_path, violations)
        assert cache_path.exists()
        assert cache_path.name == "last_analysis.json"

        loaded = load_results(tmp_path)
        assert loaded == violations

    def test_load_no_cache(self, tmp_path: Path) -> None:
        result = load_results(tmp_path)
        assert result == ()

    def test_load_corrupt_json(self, tmp_path: Path) -> None:
        cache_dir = tmp_path / ".llmcode"
        cache_dir.mkdir()
        (cache_dir / "last_analysis.json").write_text("not json", encoding="utf-8")
        result = load_results(tmp_path)
        assert result == ()

    def test_cache_contains_timestamp(self, tmp_path: Path) -> None:
        save_results(tmp_path, ())
        data = json.loads(
            (tmp_path / ".llmcode" / "last_analysis.json").read_text(encoding="utf-8")
        )
        assert "timestamp" in data
        assert "violations" in data


# ---------------------------------------------------------------------------
# File discovery tests
# ---------------------------------------------------------------------------

class TestDiscoverFiles:
    def test_skips_excluded_dirs(self, tmp_path: Path) -> None:
        # Create files in excluded directories
        for d in ("__pycache__", ".git", "node_modules", ".venv"):
            skip_dir = tmp_path / d
            skip_dir.mkdir()
            (skip_dir / "file.py").write_text("x = 1", encoding="utf-8")

        # Create a real file
        (tmp_path / "main.py").write_text("x = 1", encoding="utf-8")

        files = _discover_files(tmp_path)
        file_names = [f.name for f in files]
        assert "main.py" in file_names
        assert len(files) == 1

    def test_respects_max_files(self, tmp_path: Path) -> None:
        for i in range(10):
            (tmp_path / f"file{i}.py").write_text("x = 1", encoding="utf-8")

        files = _discover_files(tmp_path, max_files=3)
        assert len(files) == 3

    def test_empty_dir(self, tmp_path: Path) -> None:
        files = _discover_files(tmp_path)
        assert files == []


# ---------------------------------------------------------------------------
# Language detection tests
# ---------------------------------------------------------------------------

class TestLanguageForFile:
    def test_python(self, tmp_path: Path) -> None:
        assert _language_for_file(tmp_path / "foo.py") == "python"

    def test_javascript(self, tmp_path: Path) -> None:
        assert _language_for_file(tmp_path / "foo.js") == "javascript"

    def test_typescript(self, tmp_path: Path) -> None:
        assert _language_for_file(tmp_path / "foo.ts") == "javascript"

    def test_tsx(self, tmp_path: Path) -> None:
        assert _language_for_file(tmp_path / "foo.tsx") == "javascript"

    def test_other(self, tmp_path: Path) -> None:
        assert _language_for_file(tmp_path / "foo.txt") == "other"


# ---------------------------------------------------------------------------
# Engine tests
# ---------------------------------------------------------------------------

class TestRunAnalysis:
    def test_finds_violations(self, tmp_path: Path) -> None:
        # Python file with bare except
        (tmp_path / "bad.py").write_text(
            "try:\n    pass\nexcept:\n    pass\n",
            encoding="utf-8",
        )
        result = run_analysis(tmp_path)
        assert result.file_count >= 1
        assert result.duration_ms >= 0
        rule_keys = {v.rule_key for v in result.violations}
        # Should detect bare-except and empty-except at minimum
        assert "bare-except" in rule_keys

    def test_empty_dir(self, tmp_path: Path) -> None:
        result = run_analysis(tmp_path)
        assert len(result.violations) == 0
        assert result.file_count == 0

    def test_js_violations(self, tmp_path: Path) -> None:
        (tmp_path / "app.js").write_text(
            "try { foo(); } catch(e) {}\nconsole.log('hi');\n",
            encoding="utf-8",
        )
        result = run_analysis(tmp_path)
        rule_keys = {v.rule_key for v in result.violations}
        assert "empty-catch" in rule_keys
        assert "console-log" in rule_keys

    def test_caches_results(self, tmp_path: Path) -> None:
        (tmp_path / "ok.py").write_text("x = 1\n", encoding="utf-8")
        run_analysis(tmp_path)
        cache_path = tmp_path / ".llmcode" / "last_analysis.json"
        assert cache_path.exists()

    def test_detects_hardcoded_secret(self, tmp_path: Path) -> None:
        (tmp_path / "config.py").write_text(
            'api_key = "abcdefghijklmnop1234"\n',
            encoding="utf-8",
        )
        result = run_analysis(tmp_path)
        rule_keys = {v.rule_key for v in result.violations}
        assert "hardcoded-secret" in rule_keys

    def test_sorted_by_severity(self, tmp_path: Path) -> None:
        # Create file with multiple severity violations
        (tmp_path / "mixed.py").write_text(
            '# TODO: fix this\ntry:\n    pass\nexcept:\n    pass\napi_key = "abcdefghijklmnop1234"\n',
            encoding="utf-8",
        )
        result = run_analysis(tmp_path)
        severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        severities = [severity_order.get(v.severity, 9) for v in result.violations]
        assert severities == sorted(severities)

    def test_skips_binary_files(self, tmp_path: Path) -> None:
        binary_file = tmp_path / "image.py"
        binary_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
        # Should not crash
        result = run_analysis(tmp_path)
        assert result.file_count >= 1  # File is discovered
        # But no violations from unreadable file


# ---------------------------------------------------------------------------
# Diff check tests
# ---------------------------------------------------------------------------

class TestRunDiffCheck:
    def test_new_and_fixed(self, tmp_path: Path) -> None:
        # Set up cached results with a violation that will be "fixed"
        old_violations = (
            Violation(
                rule_key="bare-except",
                severity="high",
                file_path="app.py",
                line=5,
                message="Bare except clause",
            ),
        )
        save_results(tmp_path, old_violations)

        # Create the "fixed" file (no bare except)
        (tmp_path / "app.py").write_text(
            "# TODO: clean up\nx = 1\n",
            encoding="utf-8",
        )

        with patch(
            "llm_code.analysis.engine._get_changed_files",
            return_value=["app.py"],
        ):
            new_violations, fixed_violations = run_diff_check(tmp_path)

        # The bare-except was fixed
        fixed_keys = {v.rule_key for v in fixed_violations}
        assert "bare-except" in fixed_keys

        # A new todo-fixme was introduced
        new_keys = {v.rule_key for v in new_violations}
        assert "todo-fixme" in new_keys

    def test_no_changed_files(self, tmp_path: Path) -> None:
        with patch(
            "llm_code.analysis.engine._get_changed_files",
            return_value=[],
        ):
            new_violations, fixed_violations = run_diff_check(tmp_path)
        assert new_violations == []
        assert fixed_violations == []
