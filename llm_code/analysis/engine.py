"""Analysis engine -- orchestrates file discovery, rule execution, and caching."""
from __future__ import annotations

import ast
import subprocess
import time
from pathlib import Path

from llm_code.analysis.cache import load_results, save_results
from llm_code.analysis.go_rules import register_go_rules
from llm_code.analysis.rust_rules import register_rust_rules
from llm_code.analysis.js_rules import register_js_rules
from llm_code.analysis.python_rules import check_circular_import, register_python_rules
from llm_code.analysis.rules import AnalysisResult, RuleRegistry, Violation
from llm_code.analysis.universal_rules import register_universal_rules

_SKIP_DIRS = frozenset({
    ".git",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
    "dist",
    "build",
    ".egg-info",
    ".tox",
    ".mypy_cache",
    ".llm-code",
})

_PYTHON_EXTS = frozenset({".py"})
_JS_EXTS = frozenset({".js", ".ts", ".jsx", ".tsx"})
_GO_EXTS = frozenset({".go"})
_RUST_EXTS = frozenset({".rs"})
_ANALYSABLE_EXTS = _PYTHON_EXTS | _JS_EXTS | _GO_EXTS | _RUST_EXTS
_MAX_FILES = 500


def _discover_files(cwd: Path, max_files: int = _MAX_FILES) -> list[Path]:
    """Walk cwd and collect source files, skipping irrelevant directories."""
    files: list[Path] = []
    for path in sorted(cwd.rglob("*")):
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        if not path.is_file():
            continue
        if path.suffix not in _ANALYSABLE_EXTS:
            continue
        files.append(path)
        if len(files) >= max_files:
            break
    return files


def _language_for_file(path: Path) -> str:
    """Determine the language category for a file based on extension."""
    if path.suffix in _PYTHON_EXTS:
        return "python"
    if path.suffix in _JS_EXTS:
        return "javascript"
    if path.suffix in _GO_EXTS:
        return "go"
    if path.suffix in _RUST_EXTS:
        return "rust"
    return "other"


def _build_registry() -> RuleRegistry:
    """Create a fresh registry with all rules registered."""
    registry = RuleRegistry()
    register_universal_rules(registry)
    register_python_rules(registry)
    register_js_rules(registry)
    register_go_rules(registry)
    register_rust_rules(registry)
    return registry


def run_analysis(cwd: Path, max_files: int = _MAX_FILES) -> AnalysisResult:
    """Run all code analysis rules on the codebase."""
    start = time.monotonic()

    registry = _build_registry()
    files = _discover_files(cwd, max_files)

    all_violations: list[Violation] = []
    python_contents: dict[str, str] = {}

    for file_path in files:
        try:
            content = file_path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue

        rel_path = str(file_path.relative_to(cwd))
        lang = _language_for_file(file_path)

        # rules_for_language already includes "*" (universal) rules
        rules = registry.rules_for_language(lang)

        # Deduplicate by key
        seen_keys: set[str] = set()
        unique_rules = []
        for r in rules:
            if r.key not in seen_keys:
                seen_keys.add(r.key)
                unique_rules.append(r)

        # Parse AST for Python files
        tree: ast.Module | None = None
        if lang == "python":
            try:
                tree = ast.parse(content, filename=rel_path)
            except SyntaxError:
                pass

        for rule in unique_rules:
            # Skip cross-file rules in per-file loop
            if rule.key == "circular-import":
                continue
            try:
                violations = rule.check(rel_path, content, tree=tree)
            except TypeError:
                # Rule doesn't accept tree keyword
                try:
                    violations = rule.check(rel_path, content)
                except Exception:
                    continue
            except Exception:
                continue
            all_violations.extend(violations)

        if lang == "python":
            python_contents[rel_path] = content

    # Cross-file: circular imports
    if python_contents:
        try:
            circular = check_circular_import(python_contents)
            all_violations.extend(circular)
        except Exception:
            pass

    # Sort by severity then file then line
    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    all_violations.sort(
        key=lambda v: (severity_order.get(v.severity, 9), v.file_path, v.line),
    )

    result = AnalysisResult(
        violations=tuple(all_violations),
        file_count=len(files),
        duration_ms=(time.monotonic() - start) * 1000,
    )

    # Cache results
    try:
        save_results(cwd, result.violations)
    except Exception:
        pass

    return result


def run_diff_check(cwd: Path) -> tuple[list[Violation], list[Violation]]:
    """Run analysis only on changed files, compare with cached results.

    Returns (new_violations, fixed_violations).
    """
    changed_files = _get_changed_files(cwd)
    if not changed_files:
        return ([], [])

    # Load previous results
    old_violations = load_results(cwd)
    old_by_key = {(v.rule_key, v.file_path, v.line): v for v in old_violations}

    # Run analysis on changed files only
    registry = _build_registry()

    current_violations: list[Violation] = []
    for rel_path in changed_files:
        file_path = cwd / rel_path
        if not file_path.exists():
            continue
        try:
            content = file_path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue

        lang = _language_for_file(file_path)
        rules = registry.rules_for_language(lang)

        seen_keys: set[str] = set()
        unique_rules = []
        for r in rules:
            if r.key not in seen_keys:
                seen_keys.add(r.key)
                unique_rules.append(r)

        tree: ast.Module | None = None
        if lang == "python":
            try:
                tree = ast.parse(content, filename=rel_path)
            except SyntaxError:
                pass

        for rule in unique_rules:
            if rule.key == "circular-import":
                continue
            try:
                violations = rule.check(rel_path, content, tree=tree)
            except TypeError:
                try:
                    violations = rule.check(rel_path, content)
                except Exception:
                    continue
            except Exception:
                continue
            current_violations.extend(violations)

    current_by_key = {(v.rule_key, v.file_path, v.line): v for v in current_violations}

    changed_set = set(changed_files)
    new_violations = [v for k, v in current_by_key.items() if k not in old_by_key]
    fixed_violations = [
        v
        for k, v in old_by_key.items()
        if v.file_path in changed_set and k not in current_by_key
    ]

    return (new_violations, fixed_violations)


def _get_changed_files(cwd: Path) -> list[str]:
    """Get list of changed files from git (unstaged + staged)."""
    files: set[str] = set()
    for cmd in (
        ["git", "diff", "--name-only"],
        ["git", "diff", "--cached", "--name-only"],
    ):
        try:
            result = subprocess.run(
                cmd,
                cwd=str(cwd),
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                for line in result.stdout.strip().split("\n"):
                    if line.strip():
                        files.add(line.strip())
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            continue
    return sorted(files)
