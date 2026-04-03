"""Speculative executor: pre-runs a tool in an OverlayFS before user confirmation."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from llm_code.runtime.overlay import OverlayFS
from llm_code.tools.base import ToolResult

if TYPE_CHECKING:
    from llm_code.tools.base import Tool


class SpeculativeExecutor:
    """Pre-execute a tool against a Copy-on-Write overlay.

    Usage::

        executor = SpeculativeExecutor(tool, args, base_dir=cwd, session_id="abc")
        result = executor.pre_execute()   # runs tool in overlay, real FS untouched
        # … present result + pending changes to user …
        executor.confirm()               # commit overlay → real FS
        # or
        executor.deny()                  # discard overlay, nothing written

    The ``result`` returned by ``pre_execute()`` is cached; repeated calls
    return the same object without re-running the tool.
    """

    def __init__(
        self,
        tool: "Tool",
        args: dict,
        base_dir: Path,
        session_id: str,
    ) -> None:
        self._tool = tool
        self._args = args
        self.overlay = OverlayFS(base_dir=base_dir, session_id=session_id)
        self._result: ToolResult | None = None
        self._executed = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def pre_execute(self) -> ToolResult:
        """Run the tool inside the overlay (idempotent; returns cached result)."""
        if self._executed:
            assert self._result is not None
            return self._result

        self._result = self._tool.execute(self._args, overlay=self.overlay)  # type: ignore[call-arg]
        self._executed = True
        return self._result

    def confirm(self) -> None:
        """Commit the overlay to the real filesystem.

        Raises
        ------
        RuntimeError
            If ``pre_execute()`` has not been called yet.
        """
        if not self._executed:
            raise RuntimeError("call pre_execute() before confirm()")
        self.overlay.commit()

    def deny(self) -> None:
        """Discard the overlay; nothing is written to the real filesystem."""
        self.overlay.discard()

    def list_pending_changes(self) -> list[Path]:
        """Return the list of real paths that would be written on confirm()."""
        return self.overlay.list_pending()
