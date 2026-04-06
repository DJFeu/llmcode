"""MultiEditTool — atomic multi-file search-and-replace."""
from __future__ import annotations

import pathlib
from typing import TYPE_CHECKING

from pydantic import BaseModel

from llm_code.runtime.file_protection import check_write
from llm_code.tools.base import PermissionLevel, Tool, ToolResult, resolve_path
from llm_code.tools.edit_file import _apply_edit

if TYPE_CHECKING:
    from llm_code.runtime.overlay import OverlayFS

_MAX_EDITS = 20


class SingleEdit(BaseModel):
    path: str
    old: str
    new: str
    replace_all: bool = False


class MultiEditInput(BaseModel):
    edits: list[SingleEdit]


class MultiEditTool(Tool):
    @property
    def name(self) -> str:
        return "multi_edit"

    @property
    def description(self) -> str:
        return "Atomic multi-file search-and-replace. All edits succeed or none are applied."

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "edits": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "Absolute path to file"},
                            "old": {"type": "string", "description": "Text to search for"},
                            "new": {"type": "string", "description": "Replacement text"},
                            "replace_all": {"type": "boolean", "default": False},
                        },
                        "required": ["path", "old", "new"],
                    },
                    "minItems": 1,
                    "maxItems": _MAX_EDITS,
                }
            },
            "required": ["edits"],
        }

    @property
    def required_permission(self) -> PermissionLevel:
        return PermissionLevel.WORKSPACE_WRITE

    @property
    def input_model(self) -> type[MultiEditInput]:
        return MultiEditInput

    def execute(self, args: dict, overlay: "OverlayFS | None" = None) -> ToolResult:
        edits_raw = args.get("edits", [])

        if len(edits_raw) > _MAX_EDITS:
            return ToolResult(
                output=f"Too many edits ({len(edits_raw)}). Maximum is {_MAX_EDITS}.",
                is_error=True,
            )

        edits = [SingleEdit(**e) if isinstance(e, dict) else e for e in edits_raw]

        # Resolve paths (handles LLM absolute-path mistakes)
        for edit in edits:
            edit.path = str(resolve_path(edit.path))

        # Phase 1: Pre-validate (existence + write permission)
        errors: list[str] = []
        for i, edit in enumerate(edits):
            path = pathlib.Path(edit.path)
            if overlay is None:
                if not path.exists():
                    errors.append(f"Edit {i + 1}: File not found: {path}")
                    continue
            else:
                try:
                    overlay.read(path)
                except FileNotFoundError:
                    errors.append(f"Edit {i + 1}: File not found: {path}")
                    continue
            protection = check_write(str(path))
            if not protection.allowed:
                errors.append(f"Edit {i + 1}: {protection.reason}")
        if errors:
            return ToolResult(output="Validation failed:\n" + "\n".join(errors), is_error=True)

        # Phase 2: Snapshot original contents
        snapshots: dict[str, str] = {}
        for edit in edits:
            p = str(edit.path)
            if p not in snapshots:
                path = pathlib.Path(p)
                if overlay is not None:
                    snapshots[p] = overlay.read(path)
                else:
                    snapshots[p] = path.read_text(encoding="utf-8")

        # Phase 3: Apply all edits in memory
        applied: list[str] = []
        current_contents: dict[str, str] = dict(snapshots)
        for i, edit in enumerate(edits):
            p = str(edit.path)
            result = _apply_edit(current_contents[p], edit.old, edit.new, edit.replace_all)
            if not result.success:
                # Rollback: restore snapshots to real FS (overlay needs no rollback
                # — caller discards the overlay on failure)
                if overlay is None:
                    for sp, sc in snapshots.items():
                        pathlib.Path(sp).write_text(sc, encoding="utf-8")
                return ToolResult(
                    output=f"Edit {i + 1} failed ({edit.path}): {result.error}. All edits rolled back.",
                    is_error=True,
                )
            current_contents[p] = result.new_content
            applied.append(f"Edit {i + 1}: {edit.path} ({result.replaced} replacement(s))")

        # Phase 4: Write all files
        for p, content in current_contents.items():
            path = pathlib.Path(p)
            if overlay is not None:
                overlay.write(path, content)
            else:
                path.write_text(content, encoding="utf-8")

        return ToolResult(
            output=f"Applied {len(edits)} edits:\n" + "\n".join(applied),
            metadata={"edits_applied": len(edits)},
        )
