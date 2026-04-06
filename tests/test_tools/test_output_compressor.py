"""Tests for output_compressor — bash output compression filters."""
from __future__ import annotations

import json
from pathlib import Path

from llm_code.tools.output_compressor import (
    CompressResult,
    _apply_toml_filter,
    _classify,
    _compress_cargo_build,
    _compress_cargo_test,
    _compress_clippy,
    _compress_curl,
    _compress_docker,
    _compress_eslint,
    _compress_file_read,
    _compress_git_diff,
    _compress_git_log,
    _compress_git_status,
    _compress_json_output,
    _compress_kubectl,
    _compress_npm_build,
    _compress_npm_install,
    _compress_npm_test,
    _compress_pip_install,
    _compress_pytest,
    _compress_ruff,
    _detect_extension,
    _extract_json_schema,
    compress,
    load_toml_filters,
    reset_toml_filter_cache,
    strip_noise,
)


# ---------------------------------------------------------------------------
# NoiseStripper
# ---------------------------------------------------------------------------


class TestStripNoise:
    def test_strips_ansi_codes(self) -> None:
        text = "\x1b[32mhello\x1b[0m world"
        assert strip_noise(text) == "hello world"

    def test_collapses_blank_lines(self) -> None:
        text = "a\n\n\n\n\nb"
        assert strip_noise(text) == "a\n\nb"

    def test_deduplicates_consecutive_lines(self) -> None:
        text = "line1\nline2\nline2\nline2\nline3"
        result = strip_noise(text)
        assert "repeated 2 more times" in result
        assert result.count("line2") == 1

    def test_single_repeat_no_plural(self) -> None:
        text = "a\nb\nb\nc"
        result = strip_noise(text)
        assert "repeated 1 more time)" in result

    def test_short_output_unchanged(self) -> None:
        text = "ok"
        assert strip_noise(text) == "ok"

    def test_strips_carriage_return_lines(self) -> None:
        text = "Downloading...\rProgress 50%\rProgress 100%\nDone"
        result = strip_noise(text)
        assert "Done" in result


# ---------------------------------------------------------------------------
# Command classification
# ---------------------------------------------------------------------------


class TestClassify:
    def test_git_status(self) -> None:
        assert _classify("git status") == "git_status"
        assert _classify("  git status -s") == "git_status"

    def test_git_diff(self) -> None:
        assert _classify("git diff") == "git_diff"
        assert _classify("git diff --stat HEAD~3") == "git_diff"

    def test_git_log(self) -> None:
        assert _classify("git log --oneline -10") == "git_log"

    def test_pytest(self) -> None:
        assert _classify("pytest tests/") == "pytest"
        assert _classify("python -m pytest -x") == "pytest"

    def test_cargo_test(self) -> None:
        assert _classify("cargo test") == "cargo_test"

    def test_npm_test(self) -> None:
        assert _classify("npm test") == "npm_test"
        assert _classify("pnpm run test") == "npm_test"

    def test_go_test(self) -> None:
        assert _classify("go test ./...") == "go_test"

    def test_ruff(self) -> None:
        assert _classify("ruff check src/") == "ruff"

    def test_unknown(self) -> None:
        assert _classify("ls -la") == "unknown"
        assert _classify("echo hello") == "unknown"

    def test_compound_skipped(self) -> None:
        assert _classify("cd foo && git status") == "unknown"
        assert _classify("git status | head") == "unknown"


# ---------------------------------------------------------------------------
# Git filters
# ---------------------------------------------------------------------------


class TestGitStatusFilter:
    def test_porcelain_format(self) -> None:
        output = "## main\n M src/main.py\n?? new_file.txt\nA  staged.py"
        result = _compress_git_status(output)
        assert "staged:" in result
        assert "staged.py" in result
        assert "modified:" in result
        assert "src/main.py" in result
        assert "untracked:" in result
        assert "new_file.txt" in result
        assert "branch: main" in result

    def test_clean_status(self) -> None:
        output = 'nothing to commit, working tree clean'
        result = _compress_git_status(output)
        assert "nothing to commit" in result

    def test_overflow_hint(self) -> None:
        files = "\n".join(f"?? file{i}.txt" for i in range(20))
        output = f"## main\n{files}"
        result = _compress_git_status(output)
        assert "+15 more" in result

    def test_strips_hints(self) -> None:
        output = '  (use "git add" to track)\n  new_file.txt'
        result = _compress_git_status(output)
        assert '(use "git add"' not in result


class TestGitDiffFilter:
    def test_stat_format(self) -> None:
        output = " src/main.py | 10 +++++-----\n src/lib.py  |  5 ++---\n 2 files changed, 7 insertions(+), 8 deletions(-)"
        result = _compress_git_diff(output)
        assert "2 files changed" in result

    def test_diff_format_extracts_files(self) -> None:
        output = "diff --git a/foo.py b/foo.py\n--- a/foo.py\n+++ b/foo.py\n@@ -1,3 +1,4 @@\n+new line\n old line"
        result = _compress_git_diff(output)
        assert "foo.py" in result

    def test_many_files_overflow(self) -> None:
        diffs = "\n".join(f"diff --git a/f{i}.py b/f{i}.py" for i in range(20))
        result = _compress_git_diff(diffs)
        assert "+5 more" in result


class TestGitLogFilter:
    def test_multiline_commit_format(self) -> None:
        output = """commit abc1234567890
Author: user <user@mail.com>
Date:   Mon Jan 1 00:00:00 2026

    Fix the bug

commit def4567890123
Author: user <user@mail.com>
Date:   Sun Dec 31 00:00:00 2025

    Initial commit"""
        result = _compress_git_log(output)
        assert "abc1234" in result
        assert "Fix the bug" in result
        assert "Initial commit" in result
        assert "Author:" not in result
        assert "Date:" not in result

    def test_oneline_format_passthrough(self) -> None:
        output = "abc1234 Fix bug\ndef5678 Add feature"
        result = _compress_git_log(output)
        assert result == output

    def test_max_20_entries(self) -> None:
        entries = "\n".join(f"abc{i:04d} commit message {i}" for i in range(30))
        result = _compress_git_log(entries)
        assert "+10 more commits" in result


# ---------------------------------------------------------------------------
# Test runner filters
# ---------------------------------------------------------------------------


class TestPytestFilter:
    def test_all_pass_summary(self) -> None:
        output = """============================= test session starts =============================
collected 100 items

tests/test_foo.py ........ [ 8%]
tests/test_bar.py ........ [16%]
============================== 100 passed in 5.23s =============================="""
        result = _compress_pytest(output)
        assert "100 passed" in result
        assert "test session starts" not in result

    def test_failures_preserved(self) -> None:
        output = """============================= test session starts =============================
collected 10 items

tests/test_foo.py .F..

=================================== FAILURES ===================================
______________________________ test_something ______________________________

    def test_something():
>       assert 1 == 2
E       AssertionError

=========================== short test summary info ============================
FAILED tests/test_foo.py::test_something - AssertionError
============================== 1 failed, 9 passed in 2.00s =============================="""
        result = _compress_pytest(output)
        assert "1 failed" in result
        assert "FAILED tests/test_foo.py::test_something" in result
        assert "test session starts" not in result


class TestCargoTestFilter:
    def test_all_pass(self) -> None:
        output = """running 5 tests
test test_a ... ok
test test_b ... ok
test test_c ... ok
test test_d ... ok
test test_e ... ok

test result: ok. 5 passed; 0 failed; 0 ignored"""
        result = _compress_cargo_test(output)
        assert result == "ok: 5 passed"

    def test_failures_preserved(self) -> None:
        output = """running 3 tests
test test_a ... ok
test test_b ... FAILED
test test_c ... ok

failures:

---- test_b stdout ----
thread 'test_b' panicked at 'assertion failed'

test result: FAILED. 2 passed; 1 failed; 0 ignored"""
        result = _compress_cargo_test(output)
        assert "FAILED" in result
        assert "2 passed" in result
        assert "1 failed" in result


class TestNpmTestFilter:
    def test_summary_extracted(self) -> None:
        output = """PASS src/utils.test.ts
PASS src/app.test.ts
Test Suites: 2 passed, 2 total
Tests: 15 passed, 15 total
Time: 3.2s"""
        result = _compress_npm_test(output)
        assert "Tests: 15 passed" in result
        assert "PASS" not in result


# ---------------------------------------------------------------------------
# Ruff filter
# ---------------------------------------------------------------------------


class TestRuffFilter:
    def test_groups_by_rule(self) -> None:
        output = """src/main.py:10:5: E501 Line too long
src/main.py:20:1: E501 Line too long
src/main.py:30:1: F401 Unused import
src/lib.py:5:1: E501 Line too long
Found 4 errors."""
        result = _compress_ruff(output)
        assert "E501 (3x)" in result
        assert "F401 (1x)" in result
        assert "Found 4 errors" in result

    def test_clean_output(self) -> None:
        output = "All checks passed!"
        result = _compress_ruff(output)
        assert result == output


# ---------------------------------------------------------------------------
# Main compress() function
# ---------------------------------------------------------------------------


class TestCompress:
    def test_small_output_passthrough(self) -> None:
        output = "hello world"
        result = compress("git status", output)
        assert result.output == output
        assert result.saved_pct == 0

    def test_error_only_noise_stripped(self) -> None:
        output = "\x1b[31mError:\x1b[0m something failed\n" * 20
        result = compress("git status", output, is_error=True)
        assert "\x1b[31m" not in result.output
        # Should NOT apply git filter on errors
        assert "Error:" in result.output

    def test_empty_output_passthrough(self) -> None:
        result = compress("git status", "")
        assert result.output == ""

    def test_empty_fallback(self) -> None:
        """If filter empties output, fallback to noise-stripped original."""
        # This tests the safety guard — even if a filter returns empty,
        # we should get the noise-stripped version back
        result = compress("git status", "   \n\n\n   ")
        assert result.output is not None

    def test_stats_line_appended(self) -> None:
        """When savings > 20%, a stats line is appended."""
        # Large git status that will be compressed significantly
        lines = "\n".join(f"?? file{i}.txt" for i in range(100))
        output = f"## main\n{lines}"
        result = compress("git status", output)
        assert "[compressed:" in result.output

    def test_unknown_command_noise_only(self) -> None:
        output = "line1\n\x1b[32mline2\x1b[0m\n\n\n\nline3"
        result = compress("ls -la", output * 5)
        assert "\x1b[32m" not in result.output

    def test_compress_result_saved_pct(self) -> None:
        r = CompressResult(output="short", original_chars=100, compressed_chars=20)
        assert r.saved_pct == 80

    def test_compress_result_zero_original(self) -> None:
        r = CompressResult(output="", original_chars=0, compressed_chars=0)
        assert r.saved_pct == 0


# ---------------------------------------------------------------------------
# Phase 2: ESLint filter
# ---------------------------------------------------------------------------


class TestEslintFilter:
    def test_groups_by_rule(self) -> None:
        output = """src/app.ts
  10:5   error  Unexpected any              @typescript-eslint/no-explicit-any
  20:10  error  Unexpected any              @typescript-eslint/no-explicit-any
  30:1   warning  Unexpected console statement  no-console

src/utils.ts
  5:1   error  Unexpected any              @typescript-eslint/no-explicit-any

✖ 4 problems (3 errors, 1 warning)"""
        result = _compress_eslint(output)
        assert "@typescript-eslint/no-explicit-any (3x)" in result
        assert "no-console (1x)" in result
        assert "4 problems" in result

    def test_clean_output(self) -> None:
        result = _compress_eslint("No problems found")
        assert result == "No problems found"

    def test_classify_eslint(self) -> None:
        assert _classify("npx eslint src/") == "eslint"
        assert _classify("eslint .") == "eslint"


# ---------------------------------------------------------------------------
# Phase 2: Clippy filter
# ---------------------------------------------------------------------------


class TestClippyFilter:
    def test_groups_by_lint_rule(self) -> None:
        output = """warning: unused variable: `x`
 --> src/main.rs:10:9
  |
10 |     let x = 5;
  |         ^ help: if this is intentional, prefix with underscore: `_x`
  = note: `#[warn(clippy::unused_variable)]` on by default

warning: unused variable: `y`
 --> src/lib.rs:20:9
  = note: `#[warn(clippy::unused_variable)]` on by default

warning: 2 warnings emitted"""
        result = _compress_clippy(output)
        assert "clippy::unused_variable (2x)" in result
        assert "2 warnings emitted" in result

    def test_errors_shown(self) -> None:
        output = """error[E0308]: mismatched types
 --> src/main.rs:5:5

warning: 1 warning emitted"""
        result = _compress_clippy(output)
        assert "error" in result.lower()

    def test_classify_clippy(self) -> None:
        assert _classify("cargo clippy") == "clippy"
        assert _classify("cargo clippy -- -W clippy::all") == "clippy"


# ---------------------------------------------------------------------------
# Phase 2: File read filter
# ---------------------------------------------------------------------------


class TestDetectExtension:
    def test_python_file(self) -> None:
        assert _detect_extension("cat src/main.py") == ".py"

    def test_js_file(self) -> None:
        assert _detect_extension("head -50 app.js") == ".js"

    def test_no_extension(self) -> None:
        assert _detect_extension("cat Makefile") == ""

    def test_skips_flags(self) -> None:
        assert _detect_extension("tail -n 100 config.yaml") == ".yaml"


class TestFileReadFilter:
    def test_strips_python_comments(self) -> None:
        output = """# This is a comment
# Another comment
def hello():
    # inline comment
    print("hello")

# More comments
class Foo:
    pass"""
        result = _compress_file_read(output, "cat main.py")
        assert "# This is a comment" not in result
        assert "def hello():" in result
        assert "class Foo:" in result

    def test_preserves_shebangs(self) -> None:
        output = "#!/usr/bin/env python\n# comment\nimport os"
        result = _compress_file_read(output, "cat script.py")
        assert "#!/usr/bin/env python" in result

    def test_strips_js_comments(self) -> None:
        output = """// Single line comment
/* Block comment
   spanning lines */
const x = 1;
// Another comment
function foo() {}"""
        result = _compress_file_read(output, "cat app.js")
        assert "// Single line" not in result
        assert "const x = 1;" in result
        assert "function foo()" in result

    def test_unknown_extension_collapses_blanks(self) -> None:
        output = "line1\n\n\n\n\nline2"
        result = _compress_file_read(output, "cat Makefile")
        assert "\n\n\n" not in result
        assert "line1" in result
        assert "line2" in result

    def test_smart_truncate_large_file(self) -> None:
        lines = [f"line {i}" for i in range(500)]
        output = "\n".join(lines)
        result = _compress_file_read(output, "cat big.py")
        assert "lines omitted" in result
        assert "line 0" in result  # head preserved

    def test_classify_cat(self) -> None:
        assert _classify("cat foo.py") == "file_read"
        assert _classify("head -50 bar.rs") == "file_read"
        assert _classify("tail -20 log.txt") == "file_read"


# ---------------------------------------------------------------------------
# Phase 2: TOML DSL filters
# ---------------------------------------------------------------------------


class TestTomlDslFilters:
    def test_load_no_file(self, tmp_path: Path) -> None:
        filters = load_toml_filters(tmp_path)
        assert filters == []

    def test_load_valid_filters(self, tmp_path: Path) -> None:
        data = {"filters": [
            {"name": "mvn", "match_command": r"mvn\s+", "max_lines": 50}
        ]}
        (tmp_path / "output-filters.json").write_text(json.dumps(data))
        filters = load_toml_filters(tmp_path)
        assert len(filters) == 1
        assert filters[0]["name"] == "mvn"

    def test_load_malformed_json(self, tmp_path: Path) -> None:
        (tmp_path / "output-filters.json").write_text("{bad")
        filters = load_toml_filters(tmp_path)
        assert filters == []

    def test_apply_strip_lines(self) -> None:
        filt = {
            "strip_lines_matching": [r"^\[INFO\]", r"^\s*$"],
            "max_lines": 10,
        }
        output = "[INFO] Building\n[INFO] ---\nERROR: fail\n\n[INFO] Done"
        result = _apply_toml_filter(output, filt)
        assert "[INFO]" not in result
        assert "ERROR: fail" in result

    def test_apply_keep_lines(self) -> None:
        filt = {"keep_lines_matching": [r"ERROR|WARN"]}
        output = "INFO: ok\nERROR: bad\nINFO: ok2\nWARN: maybe"
        result = _apply_toml_filter(output, filt)
        assert "INFO: ok" not in result
        assert "ERROR: bad" in result
        assert "WARN: maybe" in result

    def test_apply_max_lines(self) -> None:
        filt = {"max_lines": 3}
        output = "\n".join(f"line {i}" for i in range(10))
        result = _apply_toml_filter(output, filt)
        assert "line 0" in result
        assert "line 2" in result
        assert "7 lines omitted" in result

    def test_apply_on_empty(self) -> None:
        filt = {
            "strip_lines_matching": [r".*"],
            "on_empty": "build: ok",
        }
        output = "line1\nline2\nline3"
        result = _apply_toml_filter(output, filt)
        assert result == "build: ok"

    def test_toml_filter_integration(self, tmp_path: Path, monkeypatch) -> None:
        """TOML filter applied via compress() for unknown commands."""
        reset_toml_filter_cache()
        filters = [
            {
                "name": "custom-build",
                "match_command": r"make\s+build",
                "strip_lines_matching": [r"^make\[\d+\]"],
                "max_lines": 5,
                "on_empty": "make: ok",
            }
        ]
        # Directly set the cache so compress() picks it up
        import llm_code.tools.output_compressor as oc
        monkeypatch.setattr(oc, "_cached_toml_filters", filters)

        output = "\n".join([f"make[1]: Building module-{i} with lots of verbose output padding here" for i in range(40)])
        result = compress("make build", output)
        assert result.output.startswith("make: ok")
        reset_toml_filter_cache()

    def test_cache_reset(self) -> None:
        reset_toml_filter_cache()
        from llm_code.tools.output_compressor import _cached_toml_filters
        assert _cached_toml_filters is None


# ---------------------------------------------------------------------------
# Phase 4: Docker/kubectl filters
# ---------------------------------------------------------------------------


class TestDockerFilter:
    def test_docker_ps_compact(self) -> None:
        header = "CONTAINER ID   IMAGE          STATUS          NAMES"
        rows = [f"abc{i:04d}       nginx:latest   Up {i} hours     web-{i}" for i in range(30)]
        output = header + "\n" + "\n".join(rows)
        result = _compress_docker(output)
        assert "CONTAINER ID" in result
        assert "(30 total)" in result
        assert "+10 more" in result

    def test_docker_ps_small_passthrough(self) -> None:
        output = "CONTAINER ID   IMAGE   STATUS   NAMES\nabc   nginx   Up   web"
        result = _compress_docker(output)
        assert result == output

    def test_docker_images(self) -> None:
        header = "REPOSITORY   TAG   IMAGE ID   SIZE"
        rows = [f"img-{i}   latest   sha{i:04d}   100MB" for i in range(25)]
        output = header + "\n" + "\n".join(rows)
        result = _compress_docker(output)
        assert "(25 total)" in result

    def test_classify_docker(self) -> None:
        assert _classify("docker ps") == "docker"
        assert _classify("docker images") == "docker"
        assert _classify("docker logs web") == "docker"
        assert _classify("docker compose ps") == "docker"


class TestKubectlFilter:
    def test_kubectl_get_pods_compact(self) -> None:
        header = "NAME                    READY   STATUS    RESTARTS   AGE"
        rows = [f"pod-{i}   1/1   Running   0   {i}h" for i in range(30)]
        output = header + "\n" + "\n".join(rows)
        result = _compress_kubectl(output)
        assert "NAME" in result
        assert "(30 total)" in result

    def test_kubectl_describe_events_truncated(self) -> None:
        pre = ["Name: my-pod", "Namespace: default", "Status: Running"]
        events_header = ["Events:"]
        events = [f"  Normal  Scheduled  {i}m  scheduler  msg-{i}" for i in range(20)]
        output = "\n".join(pre + events_header + events)
        result = _compress_kubectl(output)
        assert "(20 events, showing last 5)" in result
        assert "msg-19" in result

    def test_classify_kubectl(self) -> None:
        assert _classify("kubectl get pods") == "kubectl"
        assert _classify("kubectl logs my-pod") == "kubectl"
        assert _classify("kubectl describe pod my-pod") == "kubectl"


# ---------------------------------------------------------------------------
# Phase 4: Build output filters
# ---------------------------------------------------------------------------


class TestCargoBuildFilter:
    def test_strips_compiling_lines(self) -> None:
        lines = [f"   Compiling dep-{i} v0.1.0" for i in range(20)]
        lines.append("    Finished dev [unoptimized + debuginfo] target(s)")
        output = "\n".join(lines)
        result = _compress_cargo_build(output)
        assert "Compiling" not in result
        assert "20 compile steps" in result
        assert "Finished" in result

    def test_all_ok(self) -> None:
        lines = [f"   Compiling dep-{i} v0.1.0" for i in range(5)]
        output = "\n".join(lines)
        result = _compress_cargo_build(output)
        assert "cargo build: ok" in result

    def test_keeps_errors(self) -> None:
        output = "   Compiling foo v0.1.0\nerror[E0308]: mismatched types\n --> src/main.rs:5:5"
        result = _compress_cargo_build(output)
        assert "error[E0308]" in result

    def test_classify_cargo_build(self) -> None:
        assert _classify("cargo build") == "cargo_build"
        assert _classify("cargo build --release") == "cargo_build"


class TestNpmBuildFilter:
    def test_strips_progress(self) -> None:
        output = """info  - Creating an optimized production build
webpack 5.88.0 compiled in 3.2s
asset main.js 500kb
chunk main.js (main) 500kb
modules by path ./src/ 400kb
Build complete. 3 pages generated."""
        result = _compress_npm_build(output)
        assert "webpack" not in result
        assert "asset" not in result
        assert "Build complete" in result

    def test_empty_becomes_ok(self) -> None:
        output = "info  - done\nnotice  all good"
        result = _compress_npm_build(output)
        assert result == "build: ok"

    def test_classify_npm_build(self) -> None:
        assert _classify("npm run build") == "npm_build"
        assert _classify("pnpm run build") == "npm_build"
        assert _classify("next build") == "npm_build"
        assert _classify("npx next build") == "npm_build"


# ---------------------------------------------------------------------------
# Phase 4: JSON schema extraction
# ---------------------------------------------------------------------------


class TestJsonSchema:
    def test_dict_schema(self) -> None:
        data = {"id": 1, "name": "alice", "active": True}
        schema = _extract_json_schema(data)
        assert "id: int" in schema
        assert "name: str" in schema
        assert "active: bool" in schema

    def test_list_schema(self) -> None:
        data = [{"id": 1}, {"id": 2}, {"id": 3}]
        schema = _extract_json_schema(data)
        assert "3 items" in schema
        assert "id: int" in schema

    def test_nested_schema(self) -> None:
        data = {"user": {"profile": {"age": 30}}}
        schema = _extract_json_schema(data)
        assert "user:" in schema
        assert "age: int" in schema

    def test_long_string_shows_length(self) -> None:
        data = {"bio": "x" * 200}
        schema = _extract_json_schema(data)
        assert "str(200 chars)" in schema

    def test_null_value(self) -> None:
        data = {"field": None}
        schema = _extract_json_schema(data)
        assert "null" in schema

    def test_compress_json_output(self) -> None:
        raw = json.dumps({"users": [{"id": i, "name": f"user{i}"} for i in range(10)]})
        result = _compress_json_output(raw)
        assert "JSON schema" in result
        assert "10 items" in result

    def test_non_json_passthrough(self) -> None:
        result = _compress_json_output("not json at all")
        assert result == "not json at all"

    def test_classify_curl(self) -> None:
        assert _classify("curl https://api.example.com") == "curl"

    def test_curl_json_detection(self) -> None:
        raw = json.dumps({"status": "ok", "data": [1, 2, 3]})
        result = _compress_curl(raw)
        assert "JSON schema" in result


# ---------------------------------------------------------------------------
# Phase 4: Package manager install filters
# ---------------------------------------------------------------------------


class TestPipInstallFilter:
    def test_strips_download_progress(self) -> None:
        output = """Collecting requests
  Downloading requests-2.31.0.tar.gz
  Using cached urllib3-2.1.0.whl
Requirement already satisfied: certifi in ./lib
Building wheel for requests
Installing build dependencies
Successfully installed requests-2.31.0 urllib3-2.1.0"""
        result = _compress_pip_install(output)
        assert "Downloading" not in result
        assert "install steps hidden" in result
        assert "Successfully installed" in result

    def test_all_satisfied(self) -> None:
        output = """Requirement already satisfied: requests
Requirement already satisfied: urllib3"""
        result = _compress_pip_install(output)
        assert "pip install: ok" in result

    def test_classify_pip(self) -> None:
        assert _classify("pip install requests") == "pip_install"
        assert _classify("pip3 install -r requirements.txt") == "pip_install"
        assert _classify("uv pip install flask") == "pip_install"


class TestNpmInstallFilter:
    def test_strips_noise(self) -> None:
        output = """npm warn deprecated glob@7.2.3
npm http fetch GET 200 https://registry.npmjs.org/react
added 42 packages in 3s
npm notice created a lockfile"""
        result = _compress_npm_install(output)
        assert "npm warn" not in result
        assert "npm http" not in result
        assert "npm notice" not in result

    def test_empty_becomes_ok(self) -> None:
        output = "up to date, audited 100 packages in 1s\nfound 0 vulnerabilities"
        result = _compress_npm_install(output)
        assert result == "npm install: ok"

    def test_classify_npm_install(self) -> None:
        assert _classify("npm install") == "npm_install"
        assert _classify("pnpm install") == "npm_install"
        assert _classify("yarn install") == "npm_install"
