"""Notebook utility — parse, format, edit, and validate Jupyter notebooks."""
from __future__ import annotations

import copy
import dataclasses
import uuid

_OUTPUT_TRUNCATION_LIMIT = 10 * 1024  # 10 KB


@dataclasses.dataclass(frozen=True)
class NotebookCell:
    index: int
    cell_type: str
    source: str
    execution_count: int | None
    output_text: str
    images: tuple[dict, ...]


def _extract_output(outputs: list[dict]) -> tuple[str, list[dict]]:
    """Extract text and images from a list of cell outputs."""
    text_parts: list[str] = []
    images: list[dict] = []

    for output in outputs:
        output_type = output.get("output_type", "")

        if output_type == "stream":
            text = output.get("text", "")
            if isinstance(text, list):
                text = "".join(text)
            text_parts.append(text)

        elif output_type in ("execute_result", "display_data"):
            data = output.get("data", {})
            # Collect images
            for media_type in ("image/png", "image/jpeg"):
                if media_type in data:
                    images.append({"media_type": media_type, "data": data[media_type]})
            # Prefer plain text representation
            if "text/plain" in data:
                text = data["text/plain"]
                if isinstance(text, list):
                    text = "".join(text)
                text_parts.append(text)

        elif output_type == "error":
            ename = output.get("ename", "Error")
            evalue = output.get("evalue", "")
            text_parts.append(f"{ename}: {evalue}")

    combined = "\n".join(text_parts)
    if len(combined) > _OUTPUT_TRUNCATION_LIMIT:
        combined = combined[:_OUTPUT_TRUNCATION_LIMIT] + "\n... [truncated]"

    return combined, images


def parse_notebook(data: dict) -> list[NotebookCell]:
    """Parse a notebook dict into a list of NotebookCell objects."""
    cells = data.get("cells", [])
    result: list[NotebookCell] = []

    for index, cell in enumerate(cells):
        cell_type = cell.get("cell_type", "code")
        source = cell.get("source", "")
        if isinstance(source, list):
            source = "".join(source)

        execution_count = cell.get("execution_count") if cell_type == "code" else None
        outputs = cell.get("outputs", []) if cell_type == "code" else []
        output_text, images = _extract_output(outputs)

        result.append(NotebookCell(
            index=index,
            cell_type=cell_type,
            source=source,
            execution_count=execution_count,
            output_text=output_text,
            images=tuple(images),
        ))

    return result


def format_cells(cells: list[NotebookCell]) -> str:
    """Format a list of NotebookCell objects into a human-readable string."""
    if not cells:
        return ""

    parts: list[str] = []
    for cell in cells:
        exec_info = f" (exec {cell.execution_count})" if cell.execution_count is not None else ""
        header = f"Cell {cell.index} [{cell.cell_type}]{exec_info}"
        body = cell.source

        section = f"{header}\n{body}"
        if cell.output_text:
            section += f"\nOutput:\n{cell.output_text}"

        parts.append(section)

    return "\n\n".join(parts)


def validate_notebook(data: dict) -> bool:
    """Return True if data is a valid notebook (nbformat >= 4 and cells is a list)."""
    if not isinstance(data, dict):
        return False
    nbformat = data.get("nbformat")
    if not isinstance(nbformat, int) or nbformat < 4:
        return False
    cells = data.get("cells")
    if not isinstance(cells, list):
        return False
    return True


def _generate_cell_id() -> str:
    """Generate a short cell ID compatible with nbformat >= 4.5."""
    return uuid.uuid4().hex[:8]


def edit_notebook(
    data: dict,
    command: str,
    cell_index: int,
    source: str | None = None,
    cell_type: str | None = None,
) -> dict:
    """Return a new notebook dict with the specified edit applied.

    Commands:
        replace — replace cell at cell_index with new source (and optionally cell_type)
        insert  — insert a new cell before cell_index
        delete  — remove the cell at cell_index
    """
    result = copy.deepcopy(data)
    cells: list[dict] = result["cells"]
    nbformat_minor: int = data.get("nbformat_minor", 0)
    needs_id = nbformat_minor >= 5

    if command == "replace":
        if cell_index < 0 or cell_index >= len(cells):
            raise IndexError(f"Cell index {cell_index} out of range (0..{len(cells) - 1})")
        cell = cells[cell_index]
        cell["source"] = source if source is not None else cell["source"]
        if cell_type is not None:
            cell["cell_type"] = cell_type
        # Reset execution metadata when replacing
        if cell.get("cell_type") == "code" and "outputs" not in cell:
            cell["outputs"] = []

    elif command == "insert":
        if cell_index < 0 or cell_index > len(cells):
            raise IndexError(f"Insert index {cell_index} out of range (0..{len(cells)})")
        resolved_type = cell_type or "code"
        new_cell: dict = {
            "cell_type": resolved_type,
            "metadata": {},
            "source": source or "",
        }
        if needs_id:
            new_cell["id"] = _generate_cell_id()
        if resolved_type == "code":
            new_cell["execution_count"] = None
            new_cell["outputs"] = []
        cells.insert(cell_index, new_cell)

    elif command == "delete":
        if cell_index < 0 or cell_index >= len(cells):
            raise IndexError(f"Cell index {cell_index} out of range (0..{len(cells) - 1})")
        cells.pop(cell_index)

    else:
        raise ValueError(f"Unknown notebook edit command: {command!r}. Use replace, insert, or delete.")

    return result
