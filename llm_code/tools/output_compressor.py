"""Compress bash tool output before sending to LLM context.

Applies command-specific filters to reduce token consumption by 60-90%
while preserving critical information.  Small outputs (<500 chars) and
error outputs pass through with only noise stripping.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CompressResult:
    output: str
    original_chars: int
    compressed_chars: int

    @property
    def saved_pct(self) -> int:
        if self.original_chars == 0:
            return 0
        return int((1 - self.compressed_chars / self.original_chars) * 100)


# ---------------------------------------------------------------------------
# ANSI / noise patterns (compiled once)
# ---------------------------------------------------------------------------

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")
_MULTI_BLANK_RE = re.compile(r"\n{3,}")
_CR_LINE_RE = re.compile(r"[^\n]*\r(?!\n)")  # carriage-return overwrite lines


# ---------------------------------------------------------------------------
# Command classification
# ---------------------------------------------------------------------------

_GIT_STATUS_RE = re.compile(r"^\s*git\s+status\b")
_GIT_DIFF_RE = re.compile(r"^\s*git\s+diff\b")
_GIT_LOG_RE = re.compile(r"^\s*git\s+log\b")
_PYTEST_RE = re.compile(r"(?:^|\s)(?:python\s+-m\s+)?pytest\b")
_CARGO_TEST_RE = re.compile(r"(?:^|\s)cargo\s+test\b")
_NPM_TEST_RE = re.compile(r"(?:^|\s)(?:npm|npx|pnpm|yarn)\s+(?:run\s+)?test\b")
_GO_TEST_RE = re.compile(r"(?:^|\s)go\s+test\b")
_RUFF_RE = re.compile(r"(?:^|\s)ruff\s+check\b")
_ESLINT_RE = re.compile(r"(?:^|\s)(?:npx\s+)?eslint\b")
_CLIPPY_RE = re.compile(r"(?:^|\s)cargo\s+clippy\b")
_CAT_RE = re.compile(r"(?:^|\s)(?:cat|head|tail)\s+")
_DOCKER_RE = re.compile(r"(?:^|\s)docker\s+(?:ps|images|logs|compose\s+ps)\b")
_KUBECTL_RE = re.compile(r"(?:^|\s)kubectl\s+(?:get|logs|describe)\b")
_CARGO_BUILD_RE = re.compile(r"(?:^|\s)cargo\s+build\b")
_NPM_BUILD_RE = re.compile(r"(?:^|\s)(?:npm|npx|pnpm|yarn)\s+(?:run\s+)?build\b")
_NEXT_BUILD_RE = re.compile(r"(?:^|\s)(?:npx\s+)?next\s+build\b")
_PIP_INSTALL_RE = re.compile(r"(?:^|\s)(?:pip|pip3|uv\s+pip)\s+install\b")
_NPM_INSTALL_RE = re.compile(r"(?:^|\s)(?:npm|pnpm|yarn)\s+install\b")
_CURL_RE = re.compile(r"(?:^|\s)curl\s+")
_COMPOUND_RE = re.compile(r"[;&|]{1,2}")


def _classify(command: str) -> str:
    """Return a filter tag for the command, or 'unknown'."""
    cmd = command.strip()
    # Skip compound commands — too risky to mis-classify
    if _COMPOUND_RE.search(cmd):
        return "unknown"
    if _GIT_STATUS_RE.search(cmd):
        return "git_status"
    if _GIT_DIFF_RE.search(cmd):
        return "git_diff"
    if _GIT_LOG_RE.search(cmd):
        return "git_log"
    if _PYTEST_RE.search(cmd):
        return "pytest"
    if _CARGO_TEST_RE.search(cmd):
        return "cargo_test"
    if _NPM_TEST_RE.search(cmd):
        return "npm_test"
    if _GO_TEST_RE.search(cmd):
        return "go_test"
    if _RUFF_RE.search(cmd):
        return "ruff"
    if _ESLINT_RE.search(cmd):
        return "eslint"
    if _CLIPPY_RE.search(cmd):
        return "clippy"
    if _CAT_RE.search(cmd):
        return "file_read"
    if _DOCKER_RE.search(cmd):
        return "docker"
    if _KUBECTL_RE.search(cmd):
        return "kubectl"
    if _CARGO_BUILD_RE.search(cmd):
        return "cargo_build"
    if _NPM_BUILD_RE.search(cmd) or _NEXT_BUILD_RE.search(cmd):
        return "npm_build"
    if _PIP_INSTALL_RE.search(cmd):
        return "pip_install"
    if _NPM_INSTALL_RE.search(cmd):
        return "npm_install"
    if _CURL_RE.search(cmd):
        return "curl"
    return "unknown"


# ---------------------------------------------------------------------------
# Noise stripper (applied to ALL output)
# ---------------------------------------------------------------------------


def strip_noise(text: str) -> str:
    """Remove ANSI escapes, collapse blanks, deduplicate repeated lines."""
    text = _ANSI_RE.sub("", text)
    text = _CR_LINE_RE.sub("", text)
    text = _MULTI_BLANK_RE.sub("\n\n", text)

    # Deduplicate consecutive identical lines
    lines = text.split("\n")
    if len(lines) <= 3:
        return text

    result: list[str] = []
    prev = None
    repeat_count = 0
    for line in lines:
        if line == prev:
            repeat_count += 1
        else:
            if repeat_count > 0:
                result.append(f"  ... (repeated {repeat_count} more time{'s' if repeat_count > 1 else ''})")
            result.append(line)
            prev = line
            repeat_count = 0
    if repeat_count > 0:
        result.append(f"  ... (repeated {repeat_count} more time{'s' if repeat_count > 1 else ''})")

    return "\n".join(result)


# ---------------------------------------------------------------------------
# Git filters
# ---------------------------------------------------------------------------

_GIT_HINT_RE = re.compile(r"^\s*\(use \"git .*\)$", re.MULTILINE)


def _compress_git_status(output: str) -> str:
    """Compress git status output into grouped summary."""
    lines = output.strip().split("\n")
    if not lines:
        return output

    # Remove hint lines
    lines = [l for l in lines if not _GIT_HINT_RE.match(l)]
    lines = [l for l in lines if l.strip()]

    if not lines:
        return "nothing to commit, working tree clean"

    # Try to detect porcelain-style vs human-readable
    staged: list[str] = []
    modified: list[str] = []
    untracked: list[str] = []
    branch_line = ""

    for line in lines:
        stripped = line.strip()
        # Porcelain branch header: "## main...tracking"
        if line.startswith("## "):
            branch_line = line[3:].split("...")[0].strip()
            continue
        # Porcelain format: XY filename (exactly 2 status chars + space)
        if len(line) >= 4 and line[2] == " " and line[0] in " MADRCU?" and line[1] in " MADRCU?":
            x, y = line[0], line[1]
            fname = line[3:].strip()
            if x == "?" and y == "?":
                untracked.append(fname)
            elif x != " " and x != "?":
                staged.append(fname)
            elif y != " ":
                modified.append(fname)
        elif "modified:" in stripped:
            fname = stripped.split("modified:")[-1].strip()
            modified.append(fname)
        elif "new file:" in stripped:
            fname = stripped.split("new file:")[-1].strip()
            staged.append(fname)
        elif "deleted:" in stripped:
            fname = stripped.split("deleted:")[-1].strip()
            modified.append(fname)
        elif "Untracked files:" in stripped or "Changes not staged" in stripped or "Changes to be committed" in stripped:
            continue  # section headers
        elif "On branch" in stripped:
            branch_line = stripped.replace("On branch ", "")
        elif stripped and not stripped.startswith("("):
            # Could be an untracked file listed without ??
            if "no changes added" not in stripped.lower() and "nothing to commit" not in stripped.lower():
                untracked.append(stripped)

    parts: list[str] = []
    if branch_line:
        parts.append(f"branch: {branch_line}")

    max_show = 5

    def _format_group(label: str, files: list[str]) -> None:
        if not files:
            return
        parts.append(f"{label}: {len(files)} file{'s' if len(files) != 1 else ''}")
        for f in files[:max_show]:
            parts.append(f"  {f}")
        if len(files) > max_show:
            parts.append(f"  ... +{len(files) - max_show} more")

    _format_group("staged", staged)
    _format_group("modified", modified)
    _format_group("untracked", untracked)

    if not staged and not modified and not untracked:
        return "nothing to commit, working tree clean"

    return "\n".join(parts)


def _compress_git_diff(output: str) -> str:
    """Compress git diff to file list + stats summary."""
    lines = output.strip().split("\n")
    if not lines:
        return output

    # Look for --stat style output at the end
    stat_lines: list[str] = []
    diff_files: list[str] = []
    summary_line = ""

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("diff --git"):
            parts = stripped.split()
            if len(parts) >= 4:
                fname = parts[3].lstrip("b/")
                diff_files.append(fname)
        elif re.match(r"^\s*\d+ files? changed", stripped):
            summary_line = stripped
        elif "|" in stripped and ("++" in stripped or "--" in stripped or "Bin" in stripped):
            stat_lines.append(stripped)

    if summary_line:
        result_parts = [summary_line]
        # Show stat lines (already compact)
        max_stat = 15
        if stat_lines:
            for s in stat_lines[:max_stat]:
                result_parts.append(s)
            if len(stat_lines) > max_stat:
                result_parts.append(f"  ... +{len(stat_lines) - max_stat} more files")
        return "\n".join(result_parts)

    if diff_files:
        max_show = 15
        result_parts = [f"{len(diff_files)} file{'s' if len(diff_files) != 1 else ''} changed:"]
        for f in diff_files[:max_show]:
            result_parts.append(f"  {f}")
        if len(diff_files) > max_show:
            result_parts.append(f"  ... +{len(diff_files) - max_show} more")
        return "\n".join(result_parts)

    # No structure detected — just noise strip
    return output


def _compress_git_log(output: str) -> str:
    """Compress git log to max 20 one-line entries."""
    lines = output.strip().split("\n")
    if not lines:
        return output

    # If already one-line format (hash + message), just limit
    compact_lines: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line:
            i += 1
            continue
        # Detect multi-line commit blocks: "commit <hash>"
        if line.startswith("commit "):
            sha = line[7:14]  # short hash
            # Scan ahead for subject line
            msg = ""
            i += 1
            while i < len(lines):
                inner = lines[i].strip()
                if inner.startswith("Author:") or inner.startswith("Date:") or inner.startswith("Merge:"):
                    i += 1
                    continue
                if inner:
                    msg = inner
                    i += 1
                    break
                i += 1
            compact_lines.append(f"{sha} {msg}")
        else:
            compact_lines.append(line)
            i += 1

    max_entries = 20
    if len(compact_lines) <= max_entries:
        return "\n".join(compact_lines)

    result = compact_lines[:max_entries]
    result.append(f"... +{len(compact_lines) - max_entries} more commits")
    return "\n".join(result)


# ---------------------------------------------------------------------------
# Test runner filters
# ---------------------------------------------------------------------------


def _compress_pytest(output: str) -> str:
    """Compress pytest output: all-pass → summary; failures → keep detail."""
    lines = output.strip().split("\n")

    # Find summary line: "= N passed, M failed in X.XXs ="
    # or "= N passed in X.XXs ="
    summary_re = re.compile(r"=+\s*(.*(?:passed|failed|error).*)\s*=+")
    summary = ""
    failure_block: list[str] = []
    in_failure = False
    short_summary: list[str] = []
    in_short_summary = False

    for line in lines:
        stripped = line.strip()

        # Capture the final summary
        m = summary_re.match(stripped)
        if m:
            summary = m.group(1).strip()

        # Capture FAILURES section
        if "FAILURES" in stripped and stripped.startswith("="):
            in_failure = True
            continue
        if in_failure:
            if stripped.startswith("=") and ("short test summary" in stripped.lower() or "passed" in stripped.lower() or "error" in stripped.lower()):
                in_failure = False
            else:
                failure_block.append(line)

        # Capture short test summary
        if "short test summary" in stripped.lower() and stripped.startswith("="):
            in_short_summary = True
            continue
        if in_short_summary:
            if stripped.startswith("="):
                in_short_summary = False
            elif stripped:
                short_summary.append(stripped)

    if not summary:
        # Couldn't parse — return noise-stripped original
        return output

    # All passed
    if "failed" not in summary and "error" not in summary:
        return summary

    # Has failures — show summary + short summary + limited failure detail
    parts = [summary]
    if short_summary:
        parts.append("")
        max_failures = 10
        for s in short_summary[:max_failures]:
            parts.append(s)
        if len(short_summary) > max_failures:
            parts.append(f"... +{len(short_summary) - max_failures} more failures")

    if failure_block and not short_summary:
        # No short summary available, show limited failure block
        parts.append("")
        max_lines = 30
        for f in failure_block[:max_lines]:
            parts.append(f)
        if len(failure_block) > max_lines:
            parts.append(f"... +{len(failure_block) - max_lines} more lines")

    return "\n".join(parts)


def _compress_cargo_test(output: str) -> str:
    """Compress cargo test: all-pass → summary; failures → keep detail."""
    lines = output.strip().split("\n")

    # Look for "test result: ok. N passed; 0 failed" or similar
    result_re = re.compile(r"test result:\s*(ok|FAILED)\.\s*(\d+)\s*passed;\s*(\d+)\s*failed")
    results: list[str] = []
    failure_lines: list[str] = []
    in_failures = False
    total_passed = 0
    total_failed = 0

    for line in lines:
        stripped = line.strip()

        m = result_re.search(stripped)
        if m:
            total_passed += int(m.group(2))
            total_failed += int(m.group(3))
            results.append(stripped)
            in_failures = False
            continue

        if stripped == "failures:":
            in_failures = True
            continue
        if in_failures:
            if stripped.startswith("test result:") or stripped.startswith("running "):
                in_failures = False
            elif stripped:
                failure_lines.append(line)

    if not results:
        return output

    if total_failed == 0:
        return f"ok: {total_passed} passed"

    parts = [f"FAILED: {total_passed} passed, {total_failed} failed"]
    if failure_lines:
        max_lines = 30
        parts.append("")
        for f in failure_lines[:max_lines]:
            parts.append(f)
        if len(failure_lines) > max_lines:
            parts.append(f"... +{len(failure_lines) - max_lines} more lines")

    return "\n".join(parts)


def _compress_npm_test(output: str) -> str:
    """Compress npm/jest test output."""
    lines = output.strip().split("\n")

    # Jest summary: "Tests: N passed, M failed, P total"
    # or "Test Suites: ..."
    summary_lines: list[str] = []
    failure_lines: list[str] = []
    in_failure = False

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("Tests:") or stripped.startswith("Test Suites:") or stripped.startswith("Time:"):
            summary_lines.append(stripped)
            in_failure = False
        elif "FAIL " in stripped:
            in_failure = True
            failure_lines.append(stripped)
        elif in_failure and stripped:
            failure_lines.append(line)
        elif stripped.startswith("PASS "):
            in_failure = False

    if not summary_lines:
        return output

    if not failure_lines:
        return "\n".join(summary_lines)

    parts = summary_lines + [""]
    max_lines = 30
    for f in failure_lines[:max_lines]:
        parts.append(f)
    if len(failure_lines) > max_lines:
        parts.append(f"... +{len(failure_lines) - max_lines} more lines")

    return "\n".join(parts)


def _compress_go_test(output: str) -> str:
    """Compress go test output: pass → summary, fail → keep detail."""
    lines = output.strip().split("\n")

    pass_count = 0
    fail_lines: list[str] = []
    summary_lines: list[str] = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("ok"):
            pass_count += 1
            summary_lines.append(stripped)
        elif stripped.startswith("FAIL") or stripped.startswith("--- FAIL"):
            fail_lines.append(line)
        elif fail_lines:
            # Keep context around failures
            fail_lines.append(line)

    if not summary_lines and not fail_lines:
        return output

    if not fail_lines:
        return f"ok: {pass_count} package{'s' if pass_count != 1 else ''} passed"

    return "\n".join(fail_lines[-30:] + [""] + summary_lines)


# ---------------------------------------------------------------------------
# Lint filters
# ---------------------------------------------------------------------------


def _compress_ruff(output: str) -> str:
    """Compress ruff check output: group by rule code."""
    lines = output.strip().split("\n")
    if not lines:
        return output

    # Ruff format: "path/file.py:10:5: E501 Line too long (120 > 88)"
    rule_re = re.compile(r":\d+:\d+:\s+([A-Z]\d{3,4})\s+(.+)")
    by_rule: dict[str, int] = {}
    by_file: dict[str, int] = {}
    total = 0
    summary_line = ""

    for line in lines:
        stripped = line.strip()
        m = rule_re.search(stripped)
        if m:
            rule = m.group(1)
            by_rule[rule] = by_rule.get(rule, 0) + 1
            # Extract file path
            colon_idx = stripped.index(":")
            fpath = stripped[:colon_idx]
            by_file[fpath] = by_file.get(fpath, 0) + 1
            total += 1
        elif stripped.startswith("Found "):
            summary_line = stripped

    if total == 0:
        if summary_line:
            return summary_line
        return output

    parts: list[str] = []
    if summary_line:
        parts.append(summary_line)
    else:
        parts.append(f"ruff: {total} issue{'s' if total != 1 else ''} in {len(by_file)} file{'s' if len(by_file) != 1 else ''}")

    # Top rules
    sorted_rules = sorted(by_rule.items(), key=lambda x: x[1], reverse=True)
    parts.append("rules:")
    for rule, count in sorted_rules[:10]:
        parts.append(f"  {rule} ({count}x)")
    if len(sorted_rules) > 10:
        parts.append(f"  ... +{len(sorted_rules) - 10} more rules")

    # Top files
    sorted_files = sorted(by_file.items(), key=lambda x: x[1], reverse=True)
    parts.append("files:")
    for fpath, count in sorted_files[:5]:
        parts.append(f"  {fpath} ({count})")
    if len(sorted_files) > 5:
        parts.append(f"  ... +{len(sorted_files) - 5} more files")

    return "\n".join(parts)


def _compress_eslint(output: str) -> str:
    """Compress ESLint output: group by rule."""
    lines = output.strip().split("\n")
    if not lines:
        return output

    # ESLint default format: "  10:5  error  description  rule-name"
    rule_re = re.compile(r"^\s+(\d+:\d+)\s+(error|warning)\s+(.+?)\s{2,}(\S+)\s*$")
    by_rule: dict[str, int] = {}
    by_file: dict[str, int] = {}
    errors = 0
    warnings = 0
    current_file = ""
    summary_line = ""

    for line in lines:
        stripped = line.strip()
        # File header: starts at column 0 (no leading whitespace), contains path chars
        if line and not line[0].isspace() and not stripped.startswith("✖") and not stripped.startswith("✔"):
            if "/" in stripped or "\\" in stripped or stripped.endswith(":"):
                current_file = stripped.rstrip(":")
                continue
        m = rule_re.match(line)
        if m:
            severity, _desc, rule = m.group(2), m.group(3), m.group(4)
            by_rule[rule] = by_rule.get(rule, 0) + 1
            if current_file:
                by_file[current_file] = by_file.get(current_file, 0) + 1
            if severity == "error":
                errors += 1
            else:
                warnings += 1
        elif "problem" in stripped.lower() and ("error" in stripped.lower() or "warning" in stripped.lower()):
            summary_line = stripped

    total = errors + warnings
    if total == 0:
        return summary_line if summary_line else output

    parts: list[str] = []
    if summary_line:
        parts.append(summary_line)
    else:
        parts.append(f"eslint: {total} issue{'s' if total != 1 else ''} ({errors} error{'s' if errors != 1 else ''}, {warnings} warning{'s' if warnings != 1 else ''})")

    sorted_rules = sorted(by_rule.items(), key=lambda x: x[1], reverse=True)
    parts.append("rules:")
    for rule, count in sorted_rules[:10]:
        parts.append(f"  {rule} ({count}x)")
    if len(sorted_rules) > 10:
        parts.append(f"  ... +{len(sorted_rules) - 10} more rules")

    if by_file:
        sorted_files = sorted(by_file.items(), key=lambda x: x[1], reverse=True)
        parts.append("files:")
        for fpath, count in sorted_files[:5]:
            parts.append(f"  {fpath} ({count})")
        if len(sorted_files) > 5:
            parts.append(f"  ... +{len(sorted_files) - 5} more files")

    return "\n".join(parts)


def _compress_clippy(output: str) -> str:
    """Compress cargo clippy output: group warnings by lint rule."""
    lines = output.strip().split("\n")
    if not lines:
        return output

    # Clippy format: "warning: description" followed by
    # "  --> path:line:col" and sometimes "  = help: ..." ending with
    # "  = note: `#[warn(clippy::rule_name)]` on by default"
    clippy_rule_re = re.compile(r"#\[warn\((clippy::\w+)\)\]")
    warning_re = re.compile(r"^(warning|error)(\[(\w+)\])?:\s+(.+)")
    by_rule: dict[str, int] = {}
    error_lines: list[str] = []
    total_warnings = 0
    total_errors = 0
    summary_line = ""

    for line in lines:
        stripped = line.strip()
        # Extract clippy rule from note line
        m = clippy_rule_re.search(stripped)
        if m:
            rule = m.group(1)
            by_rule[rule] = by_rule.get(rule, 0) + 1

        # Count warnings/errors from header lines
        m2 = warning_re.match(stripped)
        if m2:
            severity = m2.group(1)
            if severity == "warning":
                total_warnings += 1
            elif severity == "error":
                total_errors += 1
                error_lines.append(line)

        # "warning: X warnings emitted"
        if "warnings emitted" in stripped or "errors emitted" in stripped:
            summary_line = stripped

    if total_warnings == 0 and total_errors == 0:
        return output

    parts: list[str] = []
    if summary_line:
        parts.append(summary_line)
    else:
        items = []
        if total_errors:
            items.append(f"{total_errors} error{'s' if total_errors != 1 else ''}")
        if total_warnings:
            items.append(f"{total_warnings} warning{'s' if total_warnings != 1 else ''}")
        parts.append(f"clippy: {', '.join(items)}")

    if by_rule:
        sorted_rules = sorted(by_rule.items(), key=lambda x: x[1], reverse=True)
        parts.append("lint rules:")
        for rule, count in sorted_rules[:10]:
            parts.append(f"  {rule} ({count}x)")
        if len(sorted_rules) > 10:
            parts.append(f"  ... +{len(sorted_rules) - 10} more rules")

    if error_lines:
        parts.append("errors:")
        for e in error_lines[:5]:
            parts.append(f"  {e.strip()}")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# File read filter
# ---------------------------------------------------------------------------

# Comment patterns by file extension
_COMMENT_PATTERNS: dict[str, tuple[str, str | None]] = {
    # extension: (line_comment, block_comment_start)
    ".py": (r"^\s*#", None),
    ".rb": (r"^\s*#", None),
    ".sh": (r"^\s*#", None),
    ".bash": (r"^\s*#", None),
    ".yaml": (r"^\s*#", None),
    ".yml": (r"^\s*#", None),
    ".toml": (r"^\s*#", None),
    ".js": (r"^\s*//", r"/\*"),
    ".ts": (r"^\s*//", r"/\*"),
    ".tsx": (r"^\s*//", r"/\*"),
    ".jsx": (r"^\s*//", r"/\*"),
    ".java": (r"^\s*//", r"/\*"),
    ".c": (r"^\s*//", r"/\*"),
    ".cpp": (r"^\s*//", r"/\*"),
    ".h": (r"^\s*//", r"/\*"),
    ".go": (r"^\s*//", r"/\*"),
    ".rs": (r"^\s*//", r"/\*"),
    ".swift": (r"^\s*//", r"/\*"),
}


def _detect_extension(command: str) -> str:
    """Extract file extension from cat/head/tail command."""
    # Look for a file path argument
    parts = command.strip().split()
    for part in parts[1:]:  # skip the command itself
        if part.startswith("-"):
            continue
        # Find extension
        dot_idx = part.rfind(".")
        if dot_idx > 0:
            return part[dot_idx:]
    return ""


def _compress_file_read(output: str, command: str = "") -> str:
    """Compress cat/head/tail output: strip comments, collapse blanks."""
    ext = _detect_extension(command)
    lines = output.split("\n")

    patterns = _COMMENT_PATTERNS.get(ext)
    if patterns is None:
        # No language detected — just collapse blanks
        return _MULTI_BLANK_RE.sub("\n\n", output)

    line_comment_re = re.compile(patterns[0])
    block_start = patterns[1]
    in_block = False
    result: list[str] = []

    for line in lines:
        stripped = line.strip()

        # Block comment handling
        if block_start and in_block:
            if "*/" in stripped:
                in_block = False
            continue
        if block_start and stripped.startswith("/*"):
            if "*/" not in stripped:
                in_block = False if "*/" in stripped else True
                in_block = "*/" not in stripped
            continue

        # Skip line comments (but keep shebangs and pragmas)
        if line_comment_re.match(line):
            if stripped.startswith("#!") or stripped.startswith("# type:") or stripped.startswith("# noqa"):
                result.append(line)
            continue

        # Skip pure blank lines beyond 2 consecutive
        result.append(line)

    # Collapse remaining blank runs
    text = "\n".join(result)
    text = _MULTI_BLANK_RE.sub("\n\n", text)

    # Smart truncate: if still very large, keep head + tail
    max_lines = 200
    final_lines = text.split("\n")
    if len(final_lines) > max_lines:
        head = final_lines[: max_lines // 2]
        tail = final_lines[-(max_lines // 4) :]
        omitted = len(final_lines) - len(head) - len(tail)
        text = "\n".join(head) + f"\n\n... ({omitted} lines omitted)\n\n" + "\n".join(tail)

    return text


# ---------------------------------------------------------------------------
# TOML DSL user-defined filters
# ---------------------------------------------------------------------------

# Cached user filters
_cached_toml_filters: list[dict] | None = None


def load_toml_filters(config_dir: Path | None = None) -> list[dict]:
    """Load user-defined output filters from ~/.llmcode/output-filters.toml.

    Expected format (TOML-like, stored as JSON for simplicity)::

        {
          "filters": [
            {
              "name": "maven-build",
              "match_command": "mvn\\\\s+",
              "strip_lines_matching": ["^\\\\[INFO\\\\] ---", "^\\\\s*$"],
              "max_lines": 50,
              "on_empty": "mvn: ok"
            }
          ]
        }

    Returns list of filter configs.
    """
    if config_dir is None:
        config_dir = Path.home() / ".llmcode"
    rules_path = config_dir / "output-filters.json"
    if not rules_path.exists():
        return []

    try:
        data = json.loads(rules_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        _log.warning("Failed to load output filters from %s: %s", rules_path, exc)
        return []

    return data.get("filters", [])


def get_toml_filters(config_dir: Path | None = None) -> list[dict]:
    """Return cached user filters."""
    global _cached_toml_filters
    if _cached_toml_filters is not None:
        return _cached_toml_filters
    _cached_toml_filters = load_toml_filters(config_dir)
    return _cached_toml_filters


def reset_toml_filter_cache() -> None:
    """Clear cached user filters (for testing)."""
    global _cached_toml_filters
    _cached_toml_filters = None


def _apply_toml_filter(output: str, filt: dict) -> str:
    """Apply a single TOML DSL filter to output."""
    lines = output.split("\n")

    # Stage 1: strip_lines_matching
    strip_patterns = filt.get("strip_lines_matching", [])
    if strip_patterns:
        compiled = [re.compile(p) for p in strip_patterns]
        lines = [l for l in lines if not any(p.search(l) for p in compiled)]

    # Stage 2: keep_lines_matching (mutually exclusive with strip)
    keep_patterns = filt.get("keep_lines_matching", [])
    if keep_patterns and not strip_patterns:
        compiled = [re.compile(p) for p in keep_patterns]
        lines = [l for l in lines if any(p.search(l) for p in compiled)]

    # Stage 3: max_lines
    max_lines = filt.get("max_lines")
    if max_lines and len(lines) > max_lines:
        omitted = len(lines) - max_lines
        lines = lines[:max_lines]
        lines.append(f"... ({omitted} lines omitted)")

    result = "\n".join(lines)

    # Stage 4: on_empty fallback
    if not result.strip():
        on_empty = filt.get("on_empty", "")
        if on_empty:
            return on_empty

    return result


def _try_toml_filters(command: str, output: str) -> str | None:
    """Try user-defined TOML filters. Returns compressed output or None."""
    filters = get_toml_filters()
    for filt in filters:
        match_cmd = filt.get("match_command", "")
        if not match_cmd:
            continue
        try:
            if re.search(match_cmd, command):
                return _apply_toml_filter(output, filt)
        except re.error:
            continue
    return None


# ---------------------------------------------------------------------------
# Docker / kubectl filters
# ---------------------------------------------------------------------------


def _compress_docker(output: str) -> str:
    """Compress docker ps/images/logs output."""
    lines = output.strip().split("\n")
    if not lines:
        return output

    # docker ps / docker images: table format — keep header + compact rows
    if lines[0].startswith("CONTAINER ID") or lines[0].startswith("REPOSITORY"):
        if len(lines) <= 6:
            return output
        header = lines[0]
        rows = [l for l in lines[1:] if l.strip()]
        result = [header]
        max_rows = 20
        for r in rows[:max_rows]:
            result.append(r)
        if len(rows) > max_rows:
            result.append(f"... +{len(rows) - max_rows} more")
        result.append(f"({len(rows)} total)")
        return "\n".join(result)

    # docker logs: deduplicate (already handled by noise stripper)
    return output


def _compress_kubectl(output: str) -> str:
    """Compress kubectl get/logs/describe output."""
    lines = output.strip().split("\n")
    if not lines:
        return output

    # kubectl get pods/services/deployments: table format
    if lines[0].startswith("NAME") or lines[0].startswith("NAMESPACE"):
        if len(lines) <= 6:
            return output
        header = lines[0]
        rows = [l for l in lines[1:] if l.strip()]
        result = [header]
        max_rows = 25
        for r in rows[:max_rows]:
            result.append(r)
        if len(rows) > max_rows:
            result.append(f"... +{len(rows) - max_rows} more")
        result.append(f"({len(rows)} total)")
        return "\n".join(result)

    # kubectl describe: keep key sections, strip Events if large
    if any("Name:" in l for l in lines[:5]):
        event_start = -1
        for i, l in enumerate(lines):
            if l.strip().startswith("Events:"):
                event_start = i
                break
        if event_start >= 0 and len(lines) - event_start > 10:
            pre = lines[:event_start + 1]
            events = [l for l in lines[event_start + 1:] if l.strip()]
            pre.append(f"  ({len(events)} events, showing last 5)")
            pre.extend(events[-5:])
            return "\n".join(pre)

    return output


# ---------------------------------------------------------------------------
# Build output filters
# ---------------------------------------------------------------------------

_BUILD_NOISE_RE = re.compile(
    r"^\s*(?:Compiling|Downloading|Downloaded|Unpacking|Linking|"
    r"Generating|Checking|Fresh|Documenting|Running `)\s",
    re.IGNORECASE,
)


def _compress_cargo_build(output: str) -> str:
    """Compress cargo build: strip Compiling/Downloading, keep errors."""
    lines = output.strip().split("\n")
    result: list[str] = []
    noise_count = 0

    for line in lines:
        if _BUILD_NOISE_RE.match(line):
            noise_count += 1
            continue
        result.append(line)

    if noise_count > 0:
        result.insert(0, f"({noise_count} compile steps hidden)")

    if not result or (len(result) == 1 and "compile steps" in result[0]):
        return f"cargo build: ok ({noise_count} crates compiled)"

    return "\n".join(result)


def _compress_npm_build(output: str) -> str:
    """Compress npm/next build output: strip progress, keep errors + summary."""
    lines = output.strip().split("\n")
    result: list[str] = []
    noise_patterns = re.compile(
        r"^\s*(?:info\s|notice\s|●|○|◐|⠋|⠙|⠹|▶|Creating|Collecting|Linting|"
        r"Compiled|webpack|asset\s|chunk\s|modules\s|runtime\s|entrypoint\s|"
        r"\s+\d+\.\d+\s*[kKmMgG]?[bB]\s)",
        re.IGNORECASE,
    )

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if noise_patterns.match(stripped):
            continue
        # Keep error/warning lines and summary
        result.append(line)

    if not result:
        return "build: ok"

    max_lines = 40
    if len(result) > max_lines:
        omitted = len(result) - max_lines
        result = result[:max_lines]
        result.append(f"... +{omitted} more lines")

    return "\n".join(result)


# ---------------------------------------------------------------------------
# JSON schema extraction
# ---------------------------------------------------------------------------


def _extract_json_schema(obj: object, depth: int = 0, max_depth: int = 4) -> str:
    """Extract a compact schema representation from a JSON value."""
    if depth > max_depth:
        return "..."
    if isinstance(obj, dict):
        if not obj:
            return "{}"
        parts: list[str] = []
        items = list(obj.items())
        max_keys = 15
        for k, v in items[:max_keys]:
            parts.append(f"{k}: {_extract_json_schema(v, depth + 1, max_depth)}")
        if len(items) > max_keys:
            parts.append(f"... +{len(items) - max_keys} keys")
        inner = ", ".join(parts)
        return "{" + inner + "}"
    if isinstance(obj, list):
        if not obj:
            return "[]"
        # Show type of first element + count
        first_schema = _extract_json_schema(obj[0], depth + 1, max_depth)
        return f"[{first_schema}, ...{len(obj)} items]"
    if isinstance(obj, str):
        if len(obj) > 50:
            return f'str({len(obj)} chars)'
        return "str"
    if isinstance(obj, bool):
        return "bool"
    if isinstance(obj, int):
        return "int"
    if isinstance(obj, float):
        return "float"
    if obj is None:
        return "null"
    return type(obj).__name__


def _compress_json_output(output: str) -> str:
    """Compress JSON output to schema representation."""
    stripped = output.strip()
    if not stripped.startswith(("{", "[")):
        return output

    try:
        data = json.loads(stripped)
    except (json.JSONDecodeError, ValueError):
        return output

    schema = _extract_json_schema(data)
    item_hint = ""
    if isinstance(data, list):
        item_hint = f" ({len(data)} items)"
    elif isinstance(data, dict):
        item_hint = f" ({len(data)} keys)"

    return f"JSON schema{item_hint}:\n{schema}"


def _compress_curl(output: str) -> str:
    """Compress curl output: auto-detect JSON + schema extraction."""
    stripped = output.strip()
    # Try JSON detection
    if stripped.startswith(("{", "[")):
        result = _compress_json_output(stripped)
        if result != stripped:
            return result
    return output


# ---------------------------------------------------------------------------
# Package manager install filters
# ---------------------------------------------------------------------------

_PIP_NOISE_RE = re.compile(
    r"^\s*(?:Downloading|Using cached|Collecting|"
    r"Requirement already satisfied|Preparing metadata|"
    r"Building wheels?|Installing build|Created wheel|"
    r"Stored in directory|$)",
    re.IGNORECASE,
)


def _compress_pip_install(output: str) -> str:
    """Compress pip install: strip download progress, keep summary."""
    lines = output.strip().split("\n")
    result: list[str] = []
    noise_count = 0

    for line in lines:
        if _PIP_NOISE_RE.match(line.strip()):
            noise_count += 1
            continue
        result.append(line)

    if noise_count > 0:
        result.insert(0, f"({noise_count} install steps hidden)")

    if not result or all("install steps" in r for r in result):
        return f"pip install: ok ({noise_count} steps)"

    return "\n".join(result)


_NPM_INSTALL_NOISE_RE = re.compile(
    r"^\s*(?:npm\s+(?:warn|notice|http)|added\s+\d+|"
    r"reified|timing|idealTree|fetch|sill|verb|"
    r"\d+ packages? (?:are )?looking|up to date|"
    r"audited|found\s+0)",
    re.IGNORECASE,
)


def _compress_npm_install(output: str) -> str:
    """Compress npm install: strip fetch/reify progress."""
    lines = output.strip().split("\n")
    result: list[str] = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if _NPM_INSTALL_NOISE_RE.match(stripped):
            continue
        result.append(line)

    if not result:
        return "npm install: ok"

    # Keep the "added N packages" summary line at the end
    return "\n".join(result)


# ---------------------------------------------------------------------------
# Filter dispatch
# ---------------------------------------------------------------------------

_FILTERS: dict[str, object] = {
    "git_status": _compress_git_status,
    "git_diff": _compress_git_diff,
    "git_log": _compress_git_log,
    "pytest": _compress_pytest,
    "cargo_test": _compress_cargo_test,
    "npm_test": _compress_npm_test,
    "go_test": _compress_go_test,
    "ruff": _compress_ruff,
    "eslint": _compress_eslint,
    "clippy": _compress_clippy,
    "docker": _compress_docker,
    "kubectl": _compress_kubectl,
    "cargo_build": _compress_cargo_build,
    "npm_build": _compress_npm_build,
    "pip_install": _compress_pip_install,
    "npm_install": _compress_npm_install,
    "curl": _compress_curl,
}

# Minimum output size to bother compressing
_MIN_COMPRESS_LEN = 500


def compress(command: str, output: str, *, is_error: bool = False) -> CompressResult:
    """Compress bash output based on command type.

    Guards:
    - output < 500 chars → passthrough (only noise strip)
    - is_error → noise strip only (preserve full error detail)
    - compressed result empty but original not → fallback to original
    - appends compression stats line when savings > 20%
    """
    original_len = len(output)

    if not output.strip():
        return CompressResult(output=output, original_chars=original_len, compressed_chars=len(output))

    # Always apply noise stripping
    cleaned = strip_noise(output)

    # Small output or error → just noise strip
    if original_len < _MIN_COMPRESS_LEN or is_error:
        return CompressResult(output=cleaned, original_chars=original_len, compressed_chars=len(cleaned))

    # Classify and apply specific filter
    tag = _classify(command)

    if tag == "file_read":
        compressed = _compress_file_read(cleaned, command)
    elif tag != "unknown":
        filter_fn = _FILTERS.get(tag)
        if filter_fn is not None:
            compressed = filter_fn(cleaned)  # type: ignore[operator]
        else:
            compressed = cleaned
    else:
        # Try user-defined TOML filters before giving up
        toml_result = _try_toml_filters(command, cleaned)
        compressed = toml_result if toml_result is not None else cleaned

    # Empty fallback: never return empty when original had content
    if not compressed.strip() and output.strip():
        compressed = cleaned

    compressed_len = len(compressed)

    # Append stats line when savings > 20%
    saved_pct = 0
    if original_len > 0 and compressed_len < original_len:
        saved_pct = int((1 - compressed_len / original_len) * 100)
        if saved_pct > 20:
            compressed = compressed.rstrip("\n") + f"\n[compressed: {original_len}→{compressed_len} chars, -{saved_pct}%]"
            compressed_len = len(compressed)

    # Track savings (fire-and-forget, never blocks)
    if saved_pct > 0:
        _track_savings(command, tag, original_len, compressed_len, saved_pct)

    return CompressResult(
        output=compressed,
        original_chars=original_len,
        compressed_chars=compressed_len,
    )


# Lazy singleton tracker
_tracker_instance: object | None = None


def _get_tracker():
    """Lazy-init the tracker to avoid import-time SQLite creation."""
    global _tracker_instance
    if _tracker_instance is None:
        from llm_code.tools.token_tracker import TokenTracker
        _tracker_instance = TokenTracker()
    return _tracker_instance


def _track_savings(command: str, filter_type: str, original: int, compressed: int, pct: int) -> None:
    """Record savings to SQLite.  Never raises."""
    try:
        _get_tracker().record(command, filter_type, original, compressed, pct)
    except Exception:
        pass
