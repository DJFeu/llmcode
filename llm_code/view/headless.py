"""Wave2-6: Headless CLI/pipe backend.

Read from stdin, write to stderr. stderr is used so piped-stdout
pipelines (e.g. ``llm-code ... | jq``) are not contaminated by
prompt text. Supports three modes:

* **TTY interactive** — full prompts with default markers and
  multi-line text via blank-line terminator.
* **Piped stdin** (``sys.stdin.isatty() == False``) — each line of
  stdin is consumed as the next answer. Missing input raises
  :class:`DialogCancelled` rather than blocking.
* **Non-interactive `assume_yes=True`** — every ``confirm`` returns
  its default, every ``select`` returns its default, every ``text``
  returns its default, every ``checklist`` returns its default
  selection (or an empty list). Used by ``--yes`` runs and CI.

No dependency on Textual or any TUI framework. Plain print / input.
"""
from __future__ import annotations

import sys
from typing import Sequence, TextIO, TypeVar

from llm_code.view.dialog_types import (
    Choice,
    DialogCancelled,
    DialogValidationError,
    TextValidator,
)

T = TypeVar("T")


class HeadlessDialogs:
    """stdin/stderr-based Dialogs impl for CI and pipe mode.

    Interactive iff ``input_stream.isatty()``. When not a TTY, each
    ``input_stream.readline()`` is consumed as the next answer; an
    empty line signals EOF and raises :class:`DialogCancelled`.

    ``assume_yes`` short-circuits every prompt to its default — this
    is what ``llm-code --yes`` uses.
    """

    def __init__(
        self,
        *,
        input_stream: TextIO | None = None,
        output_stream: TextIO | None = None,
        assume_yes: bool = False,
    ) -> None:
        self._in = input_stream if input_stream is not None else sys.stdin
        self._out = output_stream if output_stream is not None else sys.stderr
        self._assume_yes = assume_yes

    # ------------------------------------------------------------------
    # Dialogs protocol implementation
    # ------------------------------------------------------------------

    async def confirm(
        self,
        prompt: str,
        *,
        default: bool = False,
        danger: bool = False,
    ) -> bool:
        if self._assume_yes:
            return default
        marker = "[Y/n]" if default else "[y/N]"
        tag = "⚠ " if danger else ""
        self._write(f"{tag}{prompt} {marker} ")
        line = self._read_line()
        if line is None:
            raise DialogCancelled(f"headless confirm EOF: {prompt}")
        line = line.strip().lower()
        if not line:
            return default
        return line in ("y", "yes", "1", "true", "t")

    async def select(
        self,
        prompt: str,
        choices: Sequence[Choice[T]],
        *,
        default: T | None = None,
    ) -> T:
        if not choices:
            raise DialogCancelled(f"headless select: no choices for {prompt}")
        enabled = [c for c in choices if not c.disabled]
        if not enabled:
            raise DialogCancelled(f"headless select: all choices disabled for {prompt}")
        if self._assume_yes:
            for c in enabled:
                if c.value == default:
                    return c.value
            return enabled[0].value
        self._write(f"{prompt}\n")
        for idx, c in enumerate(choices, start=1):
            marker = " *" if c.value == default else "  "
            disabled = " (disabled)" if c.disabled else ""
            hint = f" — {c.hint}" if c.hint else ""
            self._write(f"{marker}{idx}. {c.label}{disabled}{hint}\n")
        self._write(f"Pick 1-{len(choices)}: ")
        line = self._read_line()
        if line is None:
            raise DialogCancelled(f"headless select EOF: {prompt}")
        line = line.strip()
        if not line and default is not None:
            return default
        try:
            idx = int(line) - 1
        except ValueError:
            raise DialogCancelled(f"headless select: non-integer input {line!r}")
        if not (0 <= idx < len(choices)):
            raise DialogCancelled(f"headless select: out of range: {line!r}")
        choice = choices[idx]
        if choice.disabled:
            raise DialogCancelled(f"headless select: choice {idx + 1} is disabled")
        return choice.value

    async def text(
        self,
        prompt: str,
        *,
        default: str = "",
        multiline: bool = False,
        validator: TextValidator | None = None,
    ) -> str:
        if self._assume_yes:
            if validator is not None:
                error = validator(default)
                if error is not None:
                    raise DialogValidationError(
                        f"headless text default failed validation: {error}"
                    )
            return default

        default_marker = f" [{default}]" if default else ""
        if multiline:
            self._write(f"{prompt}{default_marker} (end with a blank line):\n")
            lines: list[str] = []
            while True:
                line = self._read_line()
                if line is None:
                    raise DialogCancelled(f"headless text EOF: {prompt}")
                line = line.rstrip("\n")
                if line == "" and lines:
                    break
                if line == "" and not lines and default:
                    return default
                lines.append(line)
            value = "\n".join(lines)
        else:
            self._write(f"{prompt}{default_marker}: ")
            line = self._read_line()
            if line is None:
                raise DialogCancelled(f"headless text EOF: {prompt}")
            value = line.rstrip("\n")
            if not value:
                value = default

        if validator is not None:
            error = validator(value)
            if error is not None:
                raise DialogValidationError(error)
        return value

    async def checklist(
        self,
        prompt: str,
        items: Sequence[Choice[T]],
        *,
        min_select: int = 0,
        max_select: int | None = None,
    ) -> list[T]:
        if self._assume_yes:
            return []
        self._write(f"{prompt}\n")
        for idx, c in enumerate(items, start=1):
            disabled = " (disabled)" if c.disabled else ""
            hint = f" — {c.hint}" if c.hint else ""
            self._write(f"  {idx}. {c.label}{disabled}{hint}\n")
        bounds = ""
        if max_select is not None:
            bounds = f" (min {min_select}, max {max_select})"
        elif min_select:
            bounds = f" (min {min_select})"
        self._write(f"Pick comma-separated{bounds}, blank for none: ")
        line = self._read_line()
        if line is None:
            raise DialogCancelled(f"headless checklist EOF: {prompt}")
        line = line.strip()
        if not line:
            if min_select > 0:
                raise DialogCancelled(
                    f"headless checklist: need at least {min_select} selections"
                )
            return []
        try:
            indices = [int(x.strip()) - 1 for x in line.split(",") if x.strip()]
        except ValueError:
            raise DialogCancelled(
                f"headless checklist: non-integer in input {line!r}"
            )
        for i in indices:
            if not (0 <= i < len(items)):
                raise DialogCancelled(
                    f"headless checklist: out of range index {i + 1}"
                )
            if items[i].disabled:
                raise DialogCancelled(
                    f"headless checklist: choice {i + 1} is disabled"
                )
        if len(indices) < min_select:
            raise DialogCancelled(
                f"headless checklist: picked {len(indices)} < min {min_select}"
            )
        if max_select is not None and len(indices) > max_select:
            raise DialogCancelled(
                f"headless checklist: picked {len(indices)} > max {max_select}"
            )
        return [items[i].value for i in indices]

    # ------------------------------------------------------------------
    # I/O helpers
    # ------------------------------------------------------------------

    def _write(self, msg: str) -> None:
        self._out.write(msg)
        self._out.flush()

    def _read_line(self) -> str | None:
        """Read one line from input. Returns None on EOF."""
        line = self._in.readline()
        if line == "":  # EOF (not just blank line — readline returns "\n" for blank)
            return None
        return line
