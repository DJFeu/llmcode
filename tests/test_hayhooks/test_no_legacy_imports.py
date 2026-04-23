"""Guard: no surviving ``llm_code.remote`` / ``llm_code.ide`` imports (M4.11)."""
from __future__ import annotations

import re
from pathlib import Path



REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = REPO_ROOT / "llm_code"
TESTS_ROOT = REPO_ROOT / "tests"

_LEGACY_RE = re.compile(
    r"^\s*(?:from\s+llm_code\.(?:remote|ide)\b|import\s+llm_code\.(?:remote|ide)\b)",
    re.MULTILINE,
)


def _iter_python_files(root: Path):
    for path in root.rglob("*.py"):
        # Ignore caches, build dirs, and this guard test itself.
        if any(p in {"__pycache__", ".venv", "dist", "build"} for p in path.parts):
            continue
        if path.name == "test_no_legacy_imports.py":
            continue
        yield path


def _legacy_import_hits(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    return _LEGACY_RE.findall(text)


class TestNoLegacyImports:
    def test_package_has_no_legacy_imports(self):
        offenders = []
        for path in _iter_python_files(PACKAGE_ROOT):
            if _legacy_import_hits(path):
                offenders.append(str(path))
        assert offenders == [], (
            "llm_code.remote / llm_code.ide must be fully migrated "
            f"to llm_code.hayhooks; offenders: {offenders}"
        )

    def test_tests_dir_has_no_legacy_imports(self):
        offenders = []
        for path in _iter_python_files(TESTS_ROOT):
            if _legacy_import_hits(path):
                offenders.append(str(path))
        assert offenders == [], (
            f"legacy-style imports survived under tests/: {offenders}"
        )

    def test_legacy_packages_deleted(self):
        assert not (PACKAGE_ROOT / "remote").exists(), (
            "llm_code/remote/ must be deleted per Task 4.11"
        )
        assert not (PACKAGE_ROOT / "ide").exists(), (
            "llm_code/ide/ must be deleted per Task 4.11"
        )

    def test_legacy_test_dirs_deleted(self):
        assert not (TESTS_ROOT / "test_remote").exists()
        assert not (TESTS_ROOT / "test_ide").exists()
