"""Tests for Go regex-based analysis rules."""
from __future__ import annotations


def test_empty_error_check():
    from llm_code.analysis.go_rules import check_empty_error_check

    content = '''package main

func foo() error {
    err := doSomething()
    if err != nil {
    }
    return nil
}
'''
    violations = check_empty_error_check("main.go", content)
    assert len(violations) == 1
    assert violations[0].rule_key == "go-empty-error-check"
    assert violations[0].severity == "high"


def test_empty_error_check_no_match():
    from llm_code.analysis.go_rules import check_empty_error_check

    content = '''package main

func foo() error {
    err := doSomething()
    if err != nil {
        return err
    }
    return nil
}
'''
    violations = check_empty_error_check("main.go", content)
    assert violations == []


def test_fmt_println_in_prod():
    from llm_code.analysis.go_rules import check_fmt_println

    content = '''package main

import "fmt"

func main() {
    fmt.Println("debug output")
    fmt.Printf("value: %d", 42)
}
'''
    violations = check_fmt_println("cmd/server/main.go", content)
    assert len(violations) == 2
    assert violations[0].rule_key == "go-fmt-println"
    assert violations[0].severity == "low"


def test_fmt_println_skips_test_files():
    from llm_code.analysis.go_rules import check_fmt_println

    content = 'fmt.Println("ok")\n'
    violations = check_fmt_println("main_test.go", content)
    assert violations == []


def test_underscore_error():
    from llm_code.analysis.go_rules import check_underscore_error

    content = '''package main

func main() {
    _ = os.Remove("/tmp/foo")
    _ = json.Unmarshal(data, &out)
}
'''
    violations = check_underscore_error("main.go", content)
    assert len(violations) == 2
    assert violations[0].rule_key == "go-underscore-error"
    assert violations[0].severity == "medium"


def test_engine_discovers_go_files(tmp_path):
    from llm_code.analysis.engine import run_analysis

    go_file = tmp_path / "main.go"
    go_file.write_text('''package main

import "fmt"

func main() {
    fmt.Println("hello")
}
''')

    result = run_analysis(tmp_path)
    go_violations = [v for v in result.violations if v.file_path.endswith(".go")]
    assert len(go_violations) >= 1


def test_register_go_rules():
    from llm_code.analysis.go_rules import register_go_rules
    from llm_code.analysis.rules import RuleRegistry

    registry = RuleRegistry()
    register_go_rules(registry)
    go_rules = registry.rules_for_language("go")
    keys = {r.key for r in go_rules}
    assert "go-empty-error-check" in keys
    assert "go-fmt-println" in keys
    assert "go-underscore-error" in keys
