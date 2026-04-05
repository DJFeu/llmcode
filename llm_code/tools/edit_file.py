"""EditFileTool — search-and-replace within an existing file."""
from __future__ import annotations

import pathlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from pydantic import BaseModel

from llm_code.runtime.file_protection import check_write
from llm_code.tools.base import PermissionLevel, Tool, ToolResult
from llm_code.utils.errors import friendly_error
from llm_code.utils.text_normalize import normalize_for_match

if TYPE_CHECKING:
    from llm_code.runtime.overlay import OverlayFS

_MAX_FILE_BYTES = 50 * 1024 * 1024  # 50 MB


@dataclass(frozen=True)
class EditApplyResult:
    """Result of applying a search-and-replace edit to content."""

    success: bool
    new_content: str
    replaced: int = 0
    fuzzy_match: bool = False
    error: str = ""


def _apply_edit(content: str, old: str, new: str, replace_all: bool = False) -> EditApplyResult:
    """Apply search-and-replace to content string. Returns EditApplyResult."""
    # --- Exact match ---
    count = content.count(old)

    if count == 0:
        # --- Fuzzy match: quote normalization + trailing whitespace ---
        norm_content = normalize_for_match(content)
        norm_old = normalize_for_match(old)
        norm_count = norm_content.count(norm_old)

        if norm_count == 0:
            return EditApplyResult(success=False, new_content=content, error=f"Text not found: {old!r}")

        if replace_all:
            new_content = _fuzzy_replace_all(content, norm_content, norm_old, new)
            replaced = norm_count
        else:
            new_content = _fuzzy_replace_first(content, norm_content, norm_old, new)
            replaced = 1

        return EditApplyResult(success=True, new_content=new_content, replaced=replaced, fuzzy_match=True)

    if replace_all:
        new_content = content.replace(old, new)
        replaced = count
    else:
        new_content = content.replace(old, new, 1)
        replaced = 1

    return EditApplyResult(success=True, new_content=new_content, replaced=replaced)


class EditFileInput(BaseModel):
    path: str
    old: str
    new: str
    replace_all: bool = False


class EditFileTool(Tool):
    @property
    def name(self) -> str:
        return "edit_file"

    @property
    def description(self) -> str:
        return "Search and replace text within a file."

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute path to the file"},
                "old": {"type": "string", "description": "Text to search for"},
                "new": {"type": "string", "description": "Replacement text"},
                "replace_all": {
                    "type": "boolean",
                    "description": "Replace all occurrences (default false)",
                    "default": False,
                },
            },
            "required": ["path", "old", "new"],
        }

    @property
    def required_permission(self) -> PermissionLevel:
        return PermissionLevel.WORKSPACE_WRITE

    @property
    def input_model(self) -> type[EditFileInput]:
        return EditFileInput

    def execute(self, args: dict, overlay: "OverlayFS | None" = None) -> ToolResult:
        path = pathlib.Path(args["path"])
        old: str = args["old"]
        new: str = args["new"]
        replace_all: bool = bool(args.get("replace_all", False))

        protection = check_write(str(path))
        if not protection.allowed:
            return ToolResult(output=protection.reason, is_error=True)
        warning_prefix = f"[WARNING] {protection.reason}\n" if protection.severity == "warn" else ""

        # File size guard (real FS only — overlay has no on-disk size)
        if overlay is None:
            if not path.exists():
                return ToolResult(output=f"File not found: {path}", is_error=True)
            # Single stat call — capture both size and mtime together.
            st = path.stat()
            if st.st_size > _MAX_FILE_BYTES:
                return ToolResult(
                    output=f"File too large ({st.st_size} bytes, limit {_MAX_FILE_BYTES}): {path}",
                    is_error=True,
                )
            # Record mtime before read for conflict detection
            mtime_before = st.st_mtime
            try:
                content = path.read_text()
            except (PermissionError, OSError) as exc:
                return ToolResult(output=friendly_error(exc, str(path)), is_error=True)
        else:
            try:
                content = overlay.read(path)
            except FileNotFoundError:
                return ToolResult(output=f"File not found: {path}", is_error=True)
            mtime_before = None

        result = _apply_edit(content, old, new, replace_all)
        if not result.success:
            return ToolResult(
                output=f"Text not found in {path}: {old!r}",
                is_error=True,
            )
        new_content = result.new_content
        replaced = result.replaced
        fuzzy_match = result.fuzzy_match

        # --- mtime conflict check (real FS only, before write) ---
        if overlay is None and mtime_before is not None:
            current_mtime = path.stat().st_mtime
            if current_mtime != mtime_before:
                return ToolResult(
                    output=f"File was modified externally since last read: {path}",
                    is_error=True,
                )
            path.write_text(new_content)
        elif overlay is not None:
            overlay.write(path, new_content)

        # Generate structured diff
        from llm_code.utils.diff import generate_diff, count_changes

        hunks = generate_diff(content, new_content, path.name)
        adds, dels = count_changes(hunks)

        match_note = " (fuzzy match: quote normalization)" if fuzzy_match else ""
        diff_parts = [warning_prefix + f"Replaced {replaced} occurrence(s) in {path}{match_note}"]
        for line in old.splitlines()[:5]:
            diff_parts.append(f"- {line}")
        for line in new.splitlines()[:5]:
            diff_parts.append(f"+ {line}")

        return ToolResult(
            output="\n".join(diff_parts),
            metadata={
                "diff": [h.to_dict() for h in hunks],
                "additions": adds,
                "deletions": dels,
            },
        )


# ---------------------------------------------------------------------------
# Fuzzy replacement helpers
# ---------------------------------------------------------------------------

def _build_norm_to_orig_map(original: str) -> list[int]:
    """Build a mapping from each normalised-string index to its original index.

    normalize_for_match applies two transforms:
    - normalize_quotes: length-preserving (1-to-1 character replacement)
    - strip_trailing_whitespace: length-reducing (removes trailing spaces/tabs
      per line, but keeps the newline)

    We compute the map by stepping through the original character by character
    and deciding whether each character survives into the normalised string.
    """

    # First pass: quote normalisation is 1-to-1 in length, so positions match.
    # Second pass: trailing whitespace removal — skip chars that are spaces/tabs
    # which trail before a newline or end-of-string.

    # Pre-compute which original positions are stripped (trailing whitespace).
    n = len(original)
    stripped: list[bool] = [False] * n

    # Walk each line and mark trailing spaces/tabs for removal.
    i = 0
    while i < n:
        # Find end of line (next \n or end of string).
        j = i
        while j < n and original[j] != "\n":
            j += 1
        # j is now the position of \n or n.
        # Walk backwards from j-1 while space or tab.
        k = j - 1
        while k >= i and original[k] in (" ", "\t"):
            stripped[k] = True
            k -= 1
        i = j + 1  # skip past the \n

    # Build the map: norm_idx -> orig_idx for surviving characters.
    norm_to_orig: list[int] = []
    for orig_idx in range(n):
        if not stripped[orig_idx]:
            norm_to_orig.append(orig_idx)

    return norm_to_orig


def _fuzzy_replace_first(original: str, norm_original: str, norm_old: str, new: str) -> str:
    """Replace the first occurrence of norm_old in the original string.

    Uses the normalised strings to locate the span, then maps the normalised
    positions back to the original content positions.
    """
    norm_idx = norm_original.find(norm_old)
    if norm_idx == -1:
        return original

    norm_end = norm_idx + len(norm_old)
    norm_to_orig = _build_norm_to_orig_map(original)

    # Map normalised span to original span.
    orig_start = norm_to_orig[norm_idx]
    # norm_end may equal len(norm_original) when the match is at the very end.
    if norm_end < len(norm_to_orig):
        orig_end = norm_to_orig[norm_end]
    else:
        orig_end = len(original)

    return original[:orig_start] + new + original[orig_end:]


def _fuzzy_replace_all(original: str, norm_original: str, norm_old: str, new: str) -> str:
    """Replace all occurrences of norm_old in the original string."""
    norm_to_orig = _build_norm_to_orig_map(original)
    old_len = len(norm_old)

    result_parts: list[str] = []
    search_start_norm = 0
    search_start_orig = 0

    while True:
        idx = norm_original.find(norm_old, search_start_norm)
        if idx == -1:
            result_parts.append(original[search_start_orig:])
            break

        norm_end = idx + old_len
        orig_start = norm_to_orig[idx]
        orig_end = norm_to_orig[norm_end] if norm_end < len(norm_to_orig) else len(original)

        result_parts.append(original[search_start_orig:orig_start])
        result_parts.append(new)
        search_start_norm = norm_end
        search_start_orig = orig_end

    return "".join(result_parts)
