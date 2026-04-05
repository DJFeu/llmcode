"""Tests for llm_code.analysis.python_rules — Python AST-based rules."""
from __future__ import annotations

import ast
import textwrap

import pytest

from llm_code.analysis.python_rules import (
    check_bare_except,
    check_empty_except,
    check_unused_import,
    check_star_import,
    check_print_in_prod,
    check_circular_import,
    register_python_rules,
)
from llm_code.analysis.rules import RuleRegistry


def _parse(code: str) -> ast.Module:
    return ast.parse(textwrap.dedent(code))


class TestBareExcept:
    def test_detects_bare_except(self) -> None:
        code = """\
        try:
            x = 1
        except:
            pass
        """
        tree = _parse(code)
        violations = check_bare_except("app.py", textwrap.dedent(code), tree)
        assert len(violations) == 1
        assert violations[0].rule_key == "bare-except"
        assert violations[0].severity == "high"

    def test_ignores_typed_except(self) -> None:
        code = """\
        try:
            x = 1
        except ValueError:
            pass
        """
        tree = _parse(code)
        violations = check_bare_except("app.py", textwrap.dedent(code), tree)
        assert len(violations) == 0

    def test_multiple_bare_excepts(self) -> None:
        code = """\
        try:
            a()
        except:
            pass
        try:
            b()
        except:
            pass
        """
        tree = _parse(code)
        violations = check_bare_except("app.py", textwrap.dedent(code), tree)
        assert len(violations) == 2


class TestEmptyExcept:
    def test_detects_pass_only(self) -> None:
        code = """\
        try:
            x = 1
        except ValueError:
            pass
        """
        tree = _parse(code)
        violations = check_empty_except("app.py", textwrap.dedent(code), tree)
        assert len(violations) == 1
        assert violations[0].rule_key == "empty-except"
        assert violations[0].severity == "medium"

    def test_ignores_except_with_body(self) -> None:
        code = """\
        try:
            x = 1
        except ValueError:
            print("error")
        """
        tree = _parse(code)
        violations = check_empty_except("app.py", textwrap.dedent(code), tree)
        assert len(violations) == 0

    def test_detects_ellipsis_only(self) -> None:
        code = """\
        try:
            x = 1
        except ValueError:
            ...
        """
        tree = _parse(code)
        violations = check_empty_except("app.py", textwrap.dedent(code), tree)
        assert len(violations) == 1


class TestUnusedImport:
    def test_detects_unused(self) -> None:
        code = """\
        import os
        import sys
        x = sys.argv
        """
        tree = _parse(code)
        violations = check_unused_import("app.py", textwrap.dedent(code), tree)
        assert len(violations) == 1
        assert "os" in violations[0].message

    def test_all_used(self) -> None:
        code = """\
        import os
        path = os.path.join("a", "b")
        """
        tree = _parse(code)
        violations = check_unused_import("app.py", textwrap.dedent(code), tree)
        assert len(violations) == 0

    def test_skips_init_files(self) -> None:
        code = """\
        from .models import User
        from .views import index
        """
        tree = _parse(code)
        violations = check_unused_import("__init__.py", textwrap.dedent(code), tree)
        assert len(violations) == 0

    def test_from_import_unused(self) -> None:
        code = """\
        from os.path import join, exists
        result = join("a", "b")
        """
        tree = _parse(code)
        violations = check_unused_import("app.py", textwrap.dedent(code), tree)
        assert len(violations) == 1
        assert "exists" in violations[0].message


class TestStarImport:
    def test_detects_star(self) -> None:
        code = """\
        from os.path import *
        """
        tree = _parse(code)
        violations = check_star_import("app.py", textwrap.dedent(code), tree)
        assert len(violations) == 1
        assert violations[0].rule_key == "star-import"

    def test_ignores_normal_import(self) -> None:
        code = """\
        from os.path import join
        """
        tree = _parse(code)
        violations = check_star_import("app.py", textwrap.dedent(code), tree)
        assert len(violations) == 0


class TestPrintInProd:
    def test_detects_print(self) -> None:
        code = """\
        def main():
            print("hello")
        """
        tree = _parse(code)
        violations = check_print_in_prod("src/main.py", textwrap.dedent(code), tree)
        assert len(violations) == 1
        assert violations[0].rule_key == "print-in-prod"

    def test_skips_test_files(self) -> None:
        code = """\
        print("test output")
        """
        tree = _parse(code)
        violations = check_print_in_prod("tests/test_main.py", textwrap.dedent(code), tree)
        assert len(violations) == 0

    def test_ignores_non_print_calls(self) -> None:
        code = """\
        def main():
            logging.info("hello")
        """
        tree = _parse(code)
        violations = check_print_in_prod("src/main.py", textwrap.dedent(code), tree)
        assert len(violations) == 0


class TestCircularImport:
    def test_detects_cycle(self, tmp_path: "Path") -> None:
        from pathlib import Path

        # a.py imports b, b.py imports a
        (tmp_path / "a.py").write_text("import b\n")
        (tmp_path / "b.py").write_text("import a\n")

        files = {
            "a.py": (tmp_path / "a.py").read_text(),
            "b.py": (tmp_path / "b.py").read_text(),
        }
        violations = check_circular_import(files)
        assert len(violations) >= 1
        assert violations[0].rule_key == "circular-import"
        assert violations[0].severity == "high"

    def test_no_cycle(self, tmp_path: "Path") -> None:
        from pathlib import Path

        (tmp_path / "a.py").write_text("import os\n")
        (tmp_path / "b.py").write_text("import a\n")

        files = {
            "a.py": (tmp_path / "a.py").read_text(),
            "b.py": (tmp_path / "b.py").read_text(),
        }
        violations = check_circular_import(files)
        assert len(violations) == 0

    def test_three_module_cycle(self, tmp_path: "Path") -> None:
        from pathlib import Path

        (tmp_path / "a.py").write_text("import b\n")
        (tmp_path / "b.py").write_text("import c\n")
        (tmp_path / "c.py").write_text("import a\n")

        files = {
            "a.py": (tmp_path / "a.py").read_text(),
            "b.py": (tmp_path / "b.py").read_text(),
            "c.py": (tmp_path / "c.py").read_text(),
        }
        violations = check_circular_import(files)
        assert len(violations) >= 1
        # The message should show the full chain
        assert "→" in violations[0].message


class TestRegisterPythonRules:
    def test_registers_all_rules(self) -> None:
        registry = RuleRegistry()
        register_python_rules(registry)
        keys = {r.key for r in registry.all_rules()}
        expected = {"bare-except", "empty-except", "unused-import", "star-import", "print-in-prod", "circular-import"}
        assert keys == expected

    def test_all_target_python(self) -> None:
        registry = RuleRegistry()
        register_python_rules(registry)
        for rule in registry.all_rules():
            assert "python" in rule.languages
