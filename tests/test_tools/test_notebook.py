"""Tests for notebook utilities and tools — TDD."""
from __future__ import annotations

import json

import pytest

from llm_code.tools.base import PermissionLevel


# ---------------------------------------------------------------------------
# Sample notebook fixture
# ---------------------------------------------------------------------------

def _make_notebook(cells: list[dict], nbformat: int = 4, nbformat_minor: int = 5) -> dict:
    return {
        "nbformat": nbformat,
        "nbformat_minor": nbformat_minor,
        "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"}},
        "cells": cells,
    }


def _code_cell(source: str, execution_count: int | None = 1, outputs: list[dict] | None = None) -> dict:
    return {
        "cell_type": "code",
        "id": "abc123",
        "metadata": {},
        "source": source,
        "execution_count": execution_count,
        "outputs": outputs or [],
    }


def _markdown_cell(source: str) -> dict:
    return {
        "cell_type": "markdown",
        "id": "def456",
        "metadata": {},
        "source": source,
    }


# ---------------------------------------------------------------------------
# llm_code.utils.notebook — parse_notebook
# ---------------------------------------------------------------------------

class TestParseNotebook:
    def test_parses_code_cell(self):
        from llm_code.utils.notebook import parse_notebook
        nb = _make_notebook([_code_cell("x = 1")])
        cells = parse_notebook(nb)
        assert len(cells) == 1
        assert cells[0].cell_type == "code"
        assert cells[0].source == "x = 1"
        assert cells[0].index == 0
        assert cells[0].execution_count == 1

    def test_parses_markdown_cell(self):
        from llm_code.utils.notebook import parse_notebook
        nb = _make_notebook([_markdown_cell("# Hello")])
        cells = parse_notebook(nb)
        assert len(cells) == 1
        assert cells[0].cell_type == "markdown"
        assert cells[0].source == "# Hello"

    def test_parses_stream_output(self):
        from llm_code.utils.notebook import parse_notebook
        outputs = [{"output_type": "stream", "name": "stdout", "text": "hello\n"}]
        nb = _make_notebook([_code_cell("print('hello')", outputs=outputs)])
        cells = parse_notebook(nb)
        assert "hello" in cells[0].output_text

    def test_parses_execute_result_output(self):
        from llm_code.utils.notebook import parse_notebook
        outputs = [{"output_type": "execute_result", "data": {"text/plain": "42"}, "metadata": {}, "execution_count": 1}]
        nb = _make_notebook([_code_cell("6*7", outputs=outputs)])
        cells = parse_notebook(nb)
        assert "42" in cells[0].output_text

    def test_parses_display_data_output(self):
        from llm_code.utils.notebook import parse_notebook
        outputs = [{"output_type": "display_data", "data": {"text/plain": "fig"}, "metadata": {}}]
        nb = _make_notebook([_code_cell("fig", outputs=outputs)])
        cells = parse_notebook(nb)
        assert "fig" in cells[0].output_text

    def test_parses_error_output(self):
        from llm_code.utils.notebook import parse_notebook
        outputs = [{"output_type": "error", "ename": "ZeroDivisionError", "evalue": "division by zero", "traceback": []}]
        nb = _make_notebook([_code_cell("1/0", outputs=outputs)])
        cells = parse_notebook(nb)
        assert "ZeroDivisionError" in cells[0].output_text

    def test_extracts_image_from_output(self):
        from llm_code.utils.notebook import parse_notebook
        import base64
        png_b64 = base64.b64encode(b"\x89PNG").decode()
        outputs = [{"output_type": "display_data", "data": {"image/png": png_b64, "text/plain": ""}, "metadata": {}}]
        nb = _make_notebook([_code_cell("plot()", outputs=outputs)])
        cells = parse_notebook(nb)
        assert len(cells[0].images) == 1
        assert cells[0].images[0]["media_type"] == "image/png"
        assert cells[0].images[0]["data"] == png_b64

    def test_truncates_large_output(self):
        from llm_code.utils.notebook import parse_notebook
        big_text = "x" * 20000
        outputs = [{"output_type": "stream", "name": "stdout", "text": big_text}]
        nb = _make_notebook([_code_cell("print('x'*20000)", outputs=outputs)])
        cells = parse_notebook(nb)
        # Output should be truncated (well under 20KB raw)
        assert len(cells[0].output_text) < 15000

    def test_parses_multiple_cells(self):
        from llm_code.utils.notebook import parse_notebook
        nb = _make_notebook([
            _code_cell("a = 1"),
            _markdown_cell("## section"),
            _code_cell("b = 2", execution_count=2),
        ])
        cells = parse_notebook(nb)
        assert len(cells) == 3
        assert cells[0].index == 0
        assert cells[1].index == 1
        assert cells[2].index == 2

    def test_empty_notebook(self):
        from llm_code.utils.notebook import parse_notebook
        nb = _make_notebook([])
        cells = parse_notebook(nb)
        assert cells == []


# ---------------------------------------------------------------------------
# llm_code.utils.notebook — format_cells
# ---------------------------------------------------------------------------

class TestFormatCells:
    def test_formats_code_cell(self):
        from llm_code.utils.notebook import parse_notebook, format_cells
        nb = _make_notebook([_code_cell("x = 1")])
        cells = parse_notebook(nb)
        text = format_cells(cells)
        assert "Cell 0" in text
        assert "code" in text
        assert "x = 1" in text

    def test_formats_markdown_cell(self):
        from llm_code.utils.notebook import parse_notebook, format_cells
        nb = _make_notebook([_markdown_cell("# Title")])
        cells = parse_notebook(nb)
        text = format_cells(cells)
        assert "Cell 0" in text
        assert "markdown" in text
        assert "# Title" in text

    def test_includes_output(self):
        from llm_code.utils.notebook import parse_notebook, format_cells
        outputs = [{"output_type": "stream", "name": "stdout", "text": "result\n"}]
        nb = _make_notebook([_code_cell("print('result')", outputs=outputs)])
        cells = parse_notebook(nb)
        text = format_cells(cells)
        assert "result" in text
        assert "Output:" in text

    def test_includes_execution_count(self):
        from llm_code.utils.notebook import parse_notebook, format_cells
        nb = _make_notebook([_code_cell("x = 1", execution_count=5)])
        cells = parse_notebook(nb)
        text = format_cells(cells)
        assert "5" in text

    def test_empty_cells(self):
        from llm_code.utils.notebook import format_cells
        text = format_cells([])
        assert text == ""


# ---------------------------------------------------------------------------
# llm_code.utils.notebook — validate_notebook
# ---------------------------------------------------------------------------

class TestValidateNotebook:
    def test_valid_notebook_returns_true(self):
        from llm_code.utils.notebook import validate_notebook
        nb = _make_notebook([])
        assert validate_notebook(nb) is True

    def test_missing_nbformat_returns_false(self):
        from llm_code.utils.notebook import validate_notebook
        assert validate_notebook({"cells": []}) is False

    def test_nbformat_below_4_returns_false(self):
        from llm_code.utils.notebook import validate_notebook
        assert validate_notebook({"nbformat": 3, "cells": []}) is False

    def test_cells_not_list_returns_false(self):
        from llm_code.utils.notebook import validate_notebook
        assert validate_notebook({"nbformat": 4, "cells": "bad"}) is False

    def test_missing_cells_returns_false(self):
        from llm_code.utils.notebook import validate_notebook
        assert validate_notebook({"nbformat": 4}) is False


# ---------------------------------------------------------------------------
# llm_code.utils.notebook — edit_notebook
# ---------------------------------------------------------------------------

class TestEditNotebook:
    def test_replace_cell_source(self):
        from llm_code.utils.notebook import edit_notebook
        nb = _make_notebook([_code_cell("old source")])
        result = edit_notebook(nb, "replace", 0, source="new source")
        assert result["cells"][0]["source"] == "new source"

    def test_replace_does_not_mutate_original(self):
        from llm_code.utils.notebook import edit_notebook
        nb = _make_notebook([_code_cell("original")])
        _ = edit_notebook(nb, "replace", 0, source="changed")
        assert nb["cells"][0]["source"] == "original"

    def test_insert_cell_at_index(self):
        from llm_code.utils.notebook import edit_notebook
        nb = _make_notebook([_code_cell("a"), _code_cell("b")])
        result = edit_notebook(nb, "insert", 1, source="inserted", cell_type="code")
        assert len(result["cells"]) == 3
        assert result["cells"][1]["source"] == "inserted"

    def test_insert_at_end(self):
        from llm_code.utils.notebook import edit_notebook
        nb = _make_notebook([_code_cell("a")])
        result = edit_notebook(nb, "insert", 1, source="appended", cell_type="markdown")
        assert len(result["cells"]) == 2
        assert result["cells"][1]["source"] == "appended"
        assert result["cells"][1]["cell_type"] == "markdown"

    def test_delete_cell(self):
        from llm_code.utils.notebook import edit_notebook
        nb = _make_notebook([_code_cell("a"), _code_cell("b"), _code_cell("c")])
        result = edit_notebook(nb, "delete", 1)
        assert len(result["cells"]) == 2
        assert result["cells"][0]["source"] == "a"
        assert result["cells"][1]["source"] == "c"

    def test_replace_invalid_index_raises(self):
        from llm_code.utils.notebook import edit_notebook
        nb = _make_notebook([_code_cell("only")])
        with pytest.raises((IndexError, ValueError)):
            edit_notebook(nb, "replace", 5, source="x")

    def test_delete_invalid_index_raises(self):
        from llm_code.utils.notebook import edit_notebook
        nb = _make_notebook([_code_cell("only")])
        with pytest.raises((IndexError, ValueError)):
            edit_notebook(nb, "delete", 5)

    def test_replace_cell_type(self):
        from llm_code.utils.notebook import edit_notebook
        nb = _make_notebook([_code_cell("x = 1")])
        result = edit_notebook(nb, "replace", 0, source="# heading", cell_type="markdown")
        assert result["cells"][0]["cell_type"] == "markdown"

    def test_nbformat_45_generates_cell_id(self):
        from llm_code.utils.notebook import edit_notebook
        nb = _make_notebook([], nbformat_minor=5)
        result = edit_notebook(nb, "insert", 0, source="new cell", cell_type="code")
        assert "id" in result["cells"][0]
        assert len(result["cells"][0]["id"]) > 0


# ---------------------------------------------------------------------------
# NotebookReadTool
# ---------------------------------------------------------------------------

class TestNotebookReadTool:
    def test_name(self):
        from llm_code.tools.notebook_read import NotebookReadTool
        assert NotebookReadTool().name == "notebook_read"

    def test_permission_is_read_only(self):
        from llm_code.tools.notebook_read import NotebookReadTool
        assert NotebookReadTool().required_permission == PermissionLevel.READ_ONLY

    def test_is_read_only(self):
        from llm_code.tools.notebook_read import NotebookReadTool
        tool = NotebookReadTool()
        assert tool.is_read_only({}) is True

    def test_is_concurrency_safe(self):
        from llm_code.tools.notebook_read import NotebookReadTool
        tool = NotebookReadTool()
        assert tool.is_concurrency_safe({}) is True

    def test_reads_notebook(self, tmp_path):
        from llm_code.tools.notebook_read import NotebookReadTool
        nb = _make_notebook([_code_cell("x = 42"), _markdown_cell("# Title")])
        f = tmp_path / "test.ipynb"
        f.write_text(json.dumps(nb))
        result = NotebookReadTool().execute({"path": str(f)})
        assert result.is_error is False
        assert "x = 42" in result.output
        assert "# Title" in result.output

    def test_missing_file_returns_error(self, tmp_path):
        from llm_code.tools.notebook_read import NotebookReadTool
        result = NotebookReadTool().execute({"path": str(tmp_path / "missing.ipynb")})
        assert result.is_error is True

    def test_invalid_json_returns_error(self, tmp_path):
        from llm_code.tools.notebook_read import NotebookReadTool
        f = tmp_path / "bad.ipynb"
        f.write_text("not valid json")
        result = NotebookReadTool().execute({"path": str(f)})
        assert result.is_error is True

    def test_invalid_notebook_returns_error(self, tmp_path):
        from llm_code.tools.notebook_read import NotebookReadTool
        f = tmp_path / "bad.ipynb"
        f.write_text(json.dumps({"nbformat": 3, "cells": []}))
        result = NotebookReadTool().execute({"path": str(f)})
        assert result.is_error is True

    def test_images_in_metadata(self, tmp_path):
        from llm_code.tools.notebook_read import NotebookReadTool
        import base64
        png_b64 = base64.b64encode(b"\x89PNG").decode()
        outputs = [{"output_type": "display_data", "data": {"image/png": png_b64, "text/plain": ""}, "metadata": {}}]
        nb = _make_notebook([_code_cell("plot()", outputs=outputs)])
        f = tmp_path / "test.ipynb"
        f.write_text(json.dumps(nb))
        result = NotebookReadTool().execute({"path": str(f)})
        assert result.metadata is not None
        assert "images" in result.metadata
        assert len(result.metadata["images"]) == 1

    def test_has_input_schema(self):
        from llm_code.tools.notebook_read import NotebookReadTool
        schema = NotebookReadTool().input_schema
        assert "path" in schema["properties"]


# ---------------------------------------------------------------------------
# NotebookEditTool
# ---------------------------------------------------------------------------

class TestNotebookEditTool:
    def test_name(self):
        from llm_code.tools.notebook_edit import NotebookEditTool
        assert NotebookEditTool().name == "notebook_edit"

    def test_permission_is_workspace_write(self):
        from llm_code.tools.notebook_edit import NotebookEditTool
        assert NotebookEditTool().required_permission == PermissionLevel.WORKSPACE_WRITE

    def test_replace_cell(self, tmp_path):
        from llm_code.tools.notebook_edit import NotebookEditTool
        nb = _make_notebook([_code_cell("old")])
        f = tmp_path / "test.ipynb"
        f.write_text(json.dumps(nb))
        result = NotebookEditTool().execute({
            "path": str(f),
            "command": "replace",
            "cell_index": 0,
            "source": "new source",
        })
        assert result.is_error is False
        saved = json.loads(f.read_text())
        assert saved["cells"][0]["source"] == "new source"

    def test_insert_cell(self, tmp_path):
        from llm_code.tools.notebook_edit import NotebookEditTool
        nb = _make_notebook([_code_cell("a"), _code_cell("b")])
        f = tmp_path / "test.ipynb"
        f.write_text(json.dumps(nb))
        result = NotebookEditTool().execute({
            "path": str(f),
            "command": "insert",
            "cell_index": 1,
            "source": "middle",
            "cell_type": "code",
        })
        assert result.is_error is False
        saved = json.loads(f.read_text())
        assert len(saved["cells"]) == 3
        assert saved["cells"][1]["source"] == "middle"

    def test_delete_cell(self, tmp_path):
        from llm_code.tools.notebook_edit import NotebookEditTool
        nb = _make_notebook([_code_cell("a"), _code_cell("b")])
        f = tmp_path / "test.ipynb"
        f.write_text(json.dumps(nb))
        result = NotebookEditTool().execute({
            "path": str(f),
            "command": "delete",
            "cell_index": 0,
        })
        assert result.is_error is False
        saved = json.loads(f.read_text())
        assert len(saved["cells"]) == 1
        assert saved["cells"][0]["source"] == "b"

    def test_missing_file_returns_error(self, tmp_path):
        from llm_code.tools.notebook_edit import NotebookEditTool
        result = NotebookEditTool().execute({
            "path": str(tmp_path / "missing.ipynb"),
            "command": "delete",
            "cell_index": 0,
        })
        assert result.is_error is True

    def test_invalid_command_returns_error(self, tmp_path):
        from llm_code.tools.notebook_edit import NotebookEditTool
        nb = _make_notebook([_code_cell("a")])
        f = tmp_path / "test.ipynb"
        f.write_text(json.dumps(nb))
        result = NotebookEditTool().execute({
            "path": str(f),
            "command": "explode",
            "cell_index": 0,
        })
        assert result.is_error is True

    def test_has_input_schema(self):
        from llm_code.tools.notebook_edit import NotebookEditTool
        schema = NotebookEditTool().input_schema
        assert "path" in schema["properties"]
        assert "command" in schema["properties"]
        assert "cell_index" in schema["properties"]

    def test_writes_with_indent(self, tmp_path):
        from llm_code.tools.notebook_edit import NotebookEditTool
        nb = _make_notebook([_code_cell("a")])
        f = tmp_path / "test.ipynb"
        f.write_text(json.dumps(nb))
        NotebookEditTool().execute({
            "path": str(f),
            "command": "replace",
            "cell_index": 0,
            "source": "new",
        })
        content = f.read_text()
        # Should be pretty-printed (has newlines)
        assert "\n" in content


# ---------------------------------------------------------------------------
# ReadFileTool delegation for .ipynb files
# ---------------------------------------------------------------------------

class TestReadFileToolNotebookDelegation:
    def test_reads_ipynb_via_read_file_tool(self, tmp_path):
        from llm_code.tools.read_file import ReadFileTool
        nb = _make_notebook([_code_cell("x = 1"), _markdown_cell("# Hello")])
        f = tmp_path / "notebook.ipynb"
        f.write_text(json.dumps(nb))
        result = ReadFileTool().execute({"path": str(f)})
        assert result.is_error is False
        assert "x = 1" in result.output
        assert "# Hello" in result.output

    def test_ipynb_images_in_metadata(self, tmp_path):
        from llm_code.tools.read_file import ReadFileTool
        import base64
        png_b64 = base64.b64encode(b"\x89PNG").decode()
        outputs = [{"output_type": "display_data", "data": {"image/png": png_b64, "text/plain": ""}, "metadata": {}}]
        nb = _make_notebook([_code_cell("plot()", outputs=outputs)])
        f = tmp_path / "test.ipynb"
        f.write_text(json.dumps(nb))
        result = ReadFileTool().execute({"path": str(f)})
        assert result.metadata is not None
        assert "images" in result.metadata

    def test_invalid_ipynb_returns_error(self, tmp_path):
        from llm_code.tools.read_file import ReadFileTool
        f = tmp_path / "bad.ipynb"
        f.write_text("not json")
        result = ReadFileTool().execute({"path": str(f)})
        assert result.is_error is True
