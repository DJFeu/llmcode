"""ToolEventRegion — Style R tool call rendering.

Default behavior (Style R, spec section 6.4):
  - Start line: ``▶ tool_name args_summary``       (dim marker)
  - Success commit: ``✓ tool_name · summary · 0.3s``  (green marker)
  - Failure commit: ``✗ tool_name · error · 1.2s · exit 125``  (red marker)

Auto-expand cases:
  1. Diff tools (edit_file / write_file / apply_patch) with a
     non-empty diff_text — render a bordered Panel with the diff
     syntax-highlighted, before the summary line
  2. Failures with any stderr — render a red-bordered Panel with
     the last 12 stderr lines, before the summary line
  3. Permissions (not implemented here — handled by the dialog
     flow in the dispatcher + M8 DialogPopover)

Elapsed time is measured from __init__ to commit. Tools that never
commit show as active forever (dispatcher is responsible for always
calling commit_success or commit_failure, which M10 wires up).
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from rich.console import Console
from rich.panel import Panel

from llm_code.view.repl import style
from llm_code.view.repl.components.progress_line import (
    render_failure,
    render_start,
    render_success,
)
from llm_code.view.repl.components.structured_diff import render_structured_diff


# Tool names that get their diff auto-expanded on success
AUTO_EXPAND_DIFF_TOOLS = frozenset({
    "edit_file",
    "write_file",
    "apply_patch",
    "edit",
    "write",
})

# Max number of stderr tail lines shown when a tool fails
MAX_STDERR_TAIL_LINES = 12

# Max number of chars in the args summary (truncated with ellipsis)
MAX_ARGS_SUMMARY_LEN = 60


def _format_args_summary(args: Dict[str, Any]) -> str:
    """Compact one-line summary of tool args for the start-line display.

    Rules:
      - path / file / command / query args get priority in output
      - Other args listed as key=value pairs
      - Total length capped at MAX_ARGS_SUMMARY_LEN with trailing ...
    """
    if not args:
        return ""
    # Priority fields first
    priority = ["path", "file", "filepath", "command", "cmd", "query", "url"]
    parts: List[str] = []
    for key in priority:
        if key in args and args[key]:
            value = str(args[key])
            if len(value) > 40:
                value = value[:37] + "..."
            parts.append(value)
            break  # only one priority field shown

    # Remaining args as k=v. When no priority field matched, parts is
    # empty and we want every arg considered for the k=v rendering.
    remaining = {k: v for k, v in args.items() if k not in priority or not parts}
    for key, value in remaining.items():
        if key in priority:
            continue
        if isinstance(value, (str, int, float, bool)):
            parts.append(f"{key}={value}")

    summary = " · ".join(parts)
    if len(summary) > MAX_ARGS_SUMMARY_LEN:
        summary = summary[: MAX_ARGS_SUMMARY_LEN - 3] + "..."
    return summary


class ToolEventRegion:
    """A single tool call's display lifecycle.

    Usage (dispatcher side):
        region = ToolEventRegion(
            console=console,
            tool_name="read_file",
            args={"path": "foo.py"},
        )
        # start line already printed
        region.feed_stdout("file contents line 1\\n")
        region.feed_stdout("file contents line 2\\n")
        region.commit_success(summary="47 lines, 320 tokens")
    """

    def __init__(
        self,
        *,
        console: Console,
        tool_name: str,
        args: Dict[str, Any],
    ) -> None:
        self._console = console
        self._tool_name = tool_name
        self._args = args
        self._stdout: List[str] = []
        self._stderr: List[str] = []
        self._diff_text: str = ""
        self._committed: bool = False
        self._success: Optional[bool] = None
        self._start_time: float = time.monotonic()
        self._summary: Optional[str] = None
        self._error: Optional[str] = None
        self._exit_code: Optional[int] = None

        # Print the start line immediately (M15: colored progress line)
        self._console.print(render_start(tool_name, args))

    @property
    def is_active(self) -> bool:
        return not self._committed

    @property
    def tool_name(self) -> str:
        return self._tool_name

    @property
    def args(self) -> Dict[str, Any]:
        return dict(self._args)

    @property
    def elapsed_seconds(self) -> float:
        return time.monotonic() - self._start_time

    # === Feed methods ===

    def feed_stdout(self, line: str) -> None:
        if self._committed:
            return
        self._stdout.append(line)

    def feed_stderr(self, line: str) -> None:
        if self._committed:
            return
        self._stderr.append(line)

    def feed_diff(self, diff_text: str) -> None:
        if self._committed:
            return
        self._diff_text = diff_text

    # === Commit methods ===

    def commit_success(
        self,
        *,
        summary: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        if self._committed:
            return
        self._committed = True
        self._success = True
        self._summary = summary

        # Auto-expand diff for diff tools
        if (
            self._tool_name in AUTO_EXPAND_DIFF_TOOLS
            and self._diff_text.strip()
        ):
            self._render_diff_panel()

        # Print the summary line (M15: colored progress line)
        summary_text = summary or self._default_summary()
        self._console.print(
            render_success(self._tool_name, summary_text, elapsed=self.elapsed_seconds)
        )

    def commit_failure(
        self,
        *,
        error: str,
        exit_code: Optional[int] = None,
    ) -> None:
        if self._committed:
            return
        self._committed = True
        self._success = False
        self._error = error
        self._exit_code = exit_code

        # Auto-expand stderr tail on failure
        if self._stderr:
            self._render_failure_panel()

        # Print the summary line (M15: colored progress line)
        self._console.print(
            render_failure(
                self._tool_name, error,
                elapsed=self.elapsed_seconds,
                exit_code=exit_code,
            )
        )

    # === Internal rendering ===

    def _default_summary(self) -> str:
        """Fall-back summary if caller didn't provide one.

        Constructs something plausible from stdout/stderr volume.
        """
        out_count = len(self._stdout)
        if out_count > 0:
            return f"{out_count} line{'s' if out_count != 1 else ''}"
        return "done"

    def _render_diff_panel(self) -> None:
        path = self._args.get("path") or self._args.get("file") or ""
        # M15: structured diff with per-line colors + line numbers
        diff_renderable = render_structured_diff(
            self._diff_text, filename=path
        )
        title_parts = f"[bold {style.palette.tool_name_fg}]{self._tool_name}[/]"
        if path:
            title_parts += f" · {path}"
        self._console.print(Panel(
            diff_renderable,
            title=title_parts,
            title_align="left",
            border_style="cyan",
        ))

    def _render_failure_panel(self) -> None:
        tail = self._stderr[-MAX_STDERR_TAIL_LINES:]
        body = "\n".join(tail)
        self._console.print(Panel(
            body,
            title=f"[bold red]✗ {self._tool_name}[/bold red]",
            title_align="left",
            border_style="red",
        ))
