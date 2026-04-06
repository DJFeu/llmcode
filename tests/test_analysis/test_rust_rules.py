"""Tests for Rust regex-based analysis rules."""
from __future__ import annotations


def test_unwrap_detection():
    from llm_code.analysis.rust_rules import check_unwrap

    content = '''fn main() {
    let data = fs::read_to_string("file.txt").unwrap();
    let parsed: i32 = data.trim().parse().unwrap();
    println!("{}", parsed);
}
'''
    violations = check_unwrap("src/main.rs", content)
    assert len(violations) == 2
    assert violations[0].rule_key == "rust-unwrap"
    assert violations[0].severity == "medium"


def test_unwrap_skips_test_files():
    from llm_code.analysis.rust_rules import check_unwrap

    content = 'let v = result.unwrap();\n'
    violations = check_unwrap("tests/integration.rs", content)
    assert violations == []


def test_expect_also_caught():
    from llm_code.analysis.rust_rules import check_unwrap

    content = 'let v = result.expect("should work");\n'
    violations = check_unwrap("src/main.rs", content)
    assert len(violations) == 1


def test_todo_macro():
    from llm_code.analysis.rust_rules import check_todo_macro

    content = '''fn process(data: &str) -> Result<()> {
    todo!()
}

fn other() {
    unimplemented!()
}
'''
    violations = check_todo_macro("src/lib.rs", content)
    assert len(violations) == 2
    assert violations[0].rule_key == "rust-todo-macro"
    assert violations[0].severity == "medium"


def test_todo_macro_no_match():
    from llm_code.analysis.rust_rules import check_todo_macro

    content = '''fn process(data: &str) -> Result<()> {
    Ok(())
}
'''
    violations = check_todo_macro("src/lib.rs", content)
    assert violations == []


def test_unsafe_block():
    from llm_code.analysis.rust_rules import check_unsafe_block

    content = '''fn main() {
    unsafe {
        let ptr = &mut data as *mut i32;
        *ptr = 42;
    }
}
'''
    violations = check_unsafe_block("src/main.rs", content)
    assert len(violations) == 1
    assert violations[0].rule_key == "rust-unsafe-block"
    assert violations[0].severity == "high"


def test_unsafe_block_no_match():
    from llm_code.analysis.rust_rules import check_unsafe_block

    content = '''fn main() {
    let x = 42;
    println!("{}", x);
}
'''
    violations = check_unsafe_block("src/main.rs", content)
    assert violations == []


def test_engine_discovers_rust_files(tmp_path):
    from llm_code.analysis.engine import run_analysis

    rs_file = tmp_path / "main.rs"
    rs_file.write_text('''fn main() {
    let data = std::fs::read_to_string("f.txt").unwrap();
    println!("{}", data);
}
''')

    result = run_analysis(tmp_path)
    rs_violations = [v for v in result.violations if v.file_path.endswith(".rs")]
    assert len(rs_violations) >= 1


def test_register_rust_rules():
    from llm_code.analysis.rust_rules import register_rust_rules
    from llm_code.analysis.rules import RuleRegistry

    registry = RuleRegistry()
    register_rust_rules(registry)
    rust_rules = registry.rules_for_language("rust")
    keys = {r.key for r in rust_rules}
    assert "rust-unwrap" in keys
    assert "rust-todo-macro" in keys
    assert "rust-unsafe-block" in keys
