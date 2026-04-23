"""Unit tests for PromptBuilder (v12 M1.1)."""
from __future__ import annotations

from pathlib import Path

import pytest
from jinja2 import TemplateNotFound, UndefinedError

from llm_code.engine.prompt_builder import PromptBuilder, render_template_file

FIXTURES = Path(__file__).parent / "fixtures" / "prompts"


class TestConstruction:
    def test_inline_template_ok(self) -> None:
        b = PromptBuilder(template="Hello {{ name }}")
        assert b.run(name="world")["prompt"] == "Hello world"

    def test_file_template_ok(self) -> None:
        b = PromptBuilder(template_path="hello.j2", templates_dir=FIXTURES)
        assert b.run(name="Adam")["prompt"] == "Hello Adam!\n"

    def test_requires_exactly_one_source(self) -> None:
        with pytest.raises(ValueError, match="exactly one"):
            PromptBuilder()

        with pytest.raises(ValueError, match="exactly one"):
            PromptBuilder(template="x", template_path="y.j2", templates_dir=FIXTURES)

    def test_template_path_missing_raises(self) -> None:
        with pytest.raises(TemplateNotFound):
            PromptBuilder(template_path="does_not_exist.j2", templates_dir=FIXTURES)


class TestRun:
    def test_run_returns_dict_with_prompt_key(self) -> None:
        result = PromptBuilder(template="hi").run()
        assert isinstance(result, dict)
        assert set(result.keys()) == {"prompt"}
        assert isinstance(result["prompt"], str)

    def test_run_substitutes_all_variables(self) -> None:
        b = PromptBuilder(template="{{ a }}-{{ b }}-{{ c }}")
        assert b.run(a=1, b=2, c=3)["prompt"] == "1-2-3"

    def test_missing_variable_raises_undefined(self) -> None:
        b = PromptBuilder(template="Hello {{ name }}")
        with pytest.raises(UndefinedError):
            b.run()  # StrictUndefined

    def test_extra_kwargs_ignored(self) -> None:
        b = PromptBuilder(template="Hello {{ name }}")
        assert b.run(name="x", unused="y")["prompt"] == "Hello x"

    def test_none_value_renders_as_none(self) -> None:
        b = PromptBuilder(template="Got: {{ v }}")
        assert b.run(v=None)["prompt"] == "Got: None"


class TestRequiredVariables:
    def test_required_enforced(self) -> None:
        b = PromptBuilder(
            template="Hello {{ name }} — {{ mood }}",
            required_variables=("name", "mood"),
        )
        with pytest.raises(ValueError, match="missing required variables"):
            b.run(name="x")  # mood missing

    def test_required_lists_all_missing_keys_sorted(self) -> None:
        b = PromptBuilder(
            template="{{ a }}{{ b }}{{ c }}",
            required_variables=("a", "b", "c"),
        )
        with pytest.raises(ValueError) as exc:
            b.run()
        assert "['a', 'b', 'c']" in str(exc.value)

    def test_required_all_satisfied(self) -> None:
        b = PromptBuilder(
            template="{{ x }}", required_variables=("x",)
        )
        assert b.run(x="ok")["prompt"] == "ok"

    def test_empty_required_no_check(self) -> None:
        b = PromptBuilder(template="no vars", required_variables=())
        assert b.run()["prompt"] == "no vars"

    def test_required_variables_property(self) -> None:
        b = PromptBuilder(template="{{ x }}", required_variables=("x", "y"))
        assert b.required_variables == frozenset({"x", "y"})


class TestDeclaredVariables:
    def test_declared_simple(self) -> None:
        b = PromptBuilder(template="Hello {{ name }}")
        assert b.declared_variables == frozenset({"name"})

    def test_declared_multiple(self) -> None:
        b = PromptBuilder(template="{{ a }} and {{ b }} and {{ a }}")
        assert b.declared_variables == frozenset({"a", "b"})

    def test_declared_inside_control_flow(self) -> None:
        b = PromptBuilder(
            template="{% for x in items %}{{ x.name }}{% endfor %}"
        )
        assert "items" in b.declared_variables

    def test_declared_empty_template(self) -> None:
        b = PromptBuilder(template="static text")
        assert b.declared_variables == frozenset()

    def test_declared_variables_from_file(self) -> None:
        b = PromptBuilder(template_path="hello.j2", templates_dir=FIXTURES)
        assert b.declared_variables == frozenset({"name"})


class TestProperties:
    def test_template_name_none_for_inline(self) -> None:
        b = PromptBuilder(template="x")
        assert b.template_name is None

    def test_template_name_set_for_file(self) -> None:
        b = PromptBuilder(template_path="hello.j2", templates_dir=FIXTURES)
        assert b.template_name == "hello.j2"

    def test_templates_dir_default(self) -> None:
        b = PromptBuilder(template="x")
        assert b.templates_dir.name == "prompts"
        assert b.templates_dir.parent.name == "engine"

    def test_templates_dir_custom(self) -> None:
        b = PromptBuilder(
            template_path="hello.j2", templates_dir=FIXTURES
        )
        assert b.templates_dir == FIXTURES


class TestWhitespaceBehaviour:
    def test_keep_trailing_newline(self) -> None:
        b = PromptBuilder(template_path="hello.j2", templates_dir=FIXTURES)
        assert b.run(name="x")["prompt"].endswith("\n")

    def test_trim_blocks_removes_newlines_after_block(self) -> None:
        b = PromptBuilder(
            template="{% if True %}\nyes\n{% endif %}\ntail",
        )
        out = b.run()["prompt"]
        assert out == "yes\ntail"

    def test_multiline_structure_preserved(self) -> None:
        b = PromptBuilder(template_path="multiline.j2", templates_dir=FIXTURES)
        out = b.run(greeting="hi", subject="world", items=["a", "b"])["prompt"]
        assert "Line 1: hi" in out
        assert "Line 2: world" in out
        assert "- a" in out
        assert "- b" in out
        assert "## Heading" in out


class TestIncludeExtends:
    def test_include_resolves(self) -> None:
        b = PromptBuilder(template_path="uses_include.j2", templates_dir=FIXTURES)
        out = b.run(label="hi")["prompt"]
        assert "Before." in out
        assert "[partial: hi]" in out
        assert "After." in out

    def test_extends_resolves(self) -> None:
        b = PromptBuilder(template_path="child.j2", templates_dir=FIXTURES)
        out = b.run(message="howdy")["prompt"]
        assert "Base intro." in out
        assert "Child says: howdy" in out
        assert "Base outro." in out


class TestAutoescapeOff:
    def test_html_not_escaped(self) -> None:
        # Prompts are plain text; < > & must not become &lt; &gt; &amp;.
        b = PromptBuilder(template="Value: {{ v }}")
        out = b.run(v="<script>x & y</script>")["prompt"]
        assert out == "Value: <script>x & y</script>"

    def test_e_filter_still_escapes_on_demand(self) -> None:
        # Authors can opt in to escaping for specific fields.
        b = PromptBuilder(template="Value: {{ v | e }}")
        out = b.run(v="<x>")["prompt"]
        assert out == "Value: &lt;x&gt;"


class TestRenderTemplateFileHelper:
    def test_helper_renders_from_file(self) -> None:
        out = render_template_file("hello.j2", templates_dir=FIXTURES, name="Z")
        assert out == "Hello Z!\n"

    def test_helper_passes_all_kwargs(self) -> None:
        out = render_template_file(
            "multiline.j2",
            templates_dir=FIXTURES,
            greeting="g",
            subject="s",
            items=[],
        )
        assert "Line 1: g" in out
        assert "Line 2: s" in out


class TestEdgeCases:
    def test_empty_inline_template(self) -> None:
        b = PromptBuilder(template="")
        assert b.run()["prompt"] == ""

    def test_large_variable_values(self) -> None:
        big = "x" * 100_000
        b = PromptBuilder(template="start {{ v }} end")
        out = b.run(v=big)["prompt"]
        assert len(out) == len("start  end") + 100_000

    def test_unicode_content(self) -> None:
        b = PromptBuilder(template="{{ msg }}")
        out = b.run(msg="你好，世界 🌏")["prompt"]
        assert out == "你好，世界 🌏"

    def test_no_side_effects_across_runs(self) -> None:
        b = PromptBuilder(template="{{ v }}")
        r1 = b.run(v=1)["prompt"]
        r2 = b.run(v=2)["prompt"]
        assert r1 == "1"
        assert r2 == "2"

    def test_template_reused_is_idempotent(self) -> None:
        b = PromptBuilder(template="{{ v }}")
        assert b.run(v="a")["prompt"] == "a"
        assert b.run(v="a")["prompt"] == "a"
