"""StatusBar — persistent bottom line with model, tokens, cost, hints."""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from textual.reactive import reactive
from textual.widget import Widget
from textual.app import RenderResult
from rich.text import Text


_PERMISSION_COLORS = {
    "build": "blue",
    "plan": "yellow",
    "suggest": "cyan",
    "yolo": "red",
}


_branch_cache: dict[str, tuple[float, str]] = {}
_BRANCH_TTL = 60.0


def _git_branch_or_worktree(cwd: str | Path) -> str:
    """Return branch name, prefixed `worktree:` if inside a git worktree.

    Cached per cwd for `_BRANCH_TTL` seconds. Returns "" if not in a repo.
    """
    key = str(cwd)
    now = time.monotonic()
    cached = _branch_cache.get(key)
    if cached and now - cached[0] < _BRANCH_TTL:
        return cached[1]
    branch = ""
    try:
        r = subprocess.run(
            ["git", "branch", "--show-current"],
            capture_output=True, text=True, cwd=key, timeout=1.0,
        )
        if r.returncode == 0:
            branch = r.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        branch = ""
    if branch:
        try:
            wt = subprocess.run(
                ["git", "rev-parse", "--git-common-dir"],
                capture_output=True, text=True, cwd=key, timeout=1.0,
            )
            git_dir = subprocess.run(
                ["git", "rev-parse", "--git-dir"],
                capture_output=True, text=True, cwd=key, timeout=1.0,
            )
            if (
                wt.returncode == 0 and git_dir.returncode == 0
                and Path(wt.stdout.strip()).resolve() != Path(git_dir.stdout.strip()).resolve()
            ):
                branch = f"worktree:{branch}"
        except (OSError, subprocess.SubprocessError):
            pass
    _branch_cache[key] = (now, branch)
    return branch


def context_meter_style(pct: float) -> str:
    """Return rich style string for a context-usage percentage (0..100)."""
    if pct < 60:
        return "dim"
    if pct < 80:
        return "yellow"
    if pct < 95:
        return "#ff8800"
    return "bold red"


class StatusBar(Widget):
    """Bottom status: model │ ↓tokens tok │ $cost │ streaming… │ /help │ Ctrl+D quit"""

    model: reactive[str] = reactive("")
    tokens: reactive[int] = reactive(0)
    cost: reactive[str] = reactive("")
    is_streaming: reactive[bool] = reactive(False)
    vim_mode: reactive[str] = reactive("")  # "" | "NORMAL" | "INSERT"
    is_local: reactive[bool] = reactive(False)
    plan_mode: reactive[str] = reactive("")  # "" | "PLAN"
    bg_tasks: reactive[int] = reactive(0)   # running/pending background tasks
    context_used: reactive[int] = reactive(0)     # tokens currently in context window
    context_limit: reactive[int] = reactive(0)    # total context window size (0 = unknown)
    turn_count: reactive[int] = reactive(0)
    permission_mode: reactive[str] = reactive("")  # build/plan/suggest/yolo
    cwd_basename: reactive[str] = reactive("")
    git_branch: reactive[str] = reactive("")
    # Seconds elapsed in the current voice recording. 0 = not recording;
    # the StatusBar segment renders only when this is positive so an
    # idle TUI keeps the bar clean.
    voice_elapsed: reactive[float] = reactive(0.0)

    DEFAULT_CSS = """
    StatusBar {
        dock: bottom;
        height: 1;
        background: $surface-darken-1;
        color: $text-muted;
        padding: 0 1;
    }
    """

    def context_pct(self) -> float:
        """Return current context-window utilization 0..100, or 0 if unknown."""
        if self.context_limit <= 0:
            return 0.0
        return min(100.0, (self.context_used / self.context_limit) * 100.0)

    def refresh_git_branch(self, cwd: str | Path | None = None) -> None:
        """Refresh cached git branch for the given cwd (defaults to os.getcwd)."""
        self.git_branch = _git_branch_or_worktree(cwd or os.getcwd())

    def _segments(self) -> list[tuple[int, str, str]]:
        """Return (priority, text, style) segments. Lower priority = drop first."""
        segs: list[tuple[int, str, str]] = []
        # Right-most first in the layout list, priority = drop-order (lower drops first)
        if self.plan_mode:
            segs.append((100, self.plan_mode, "bold yellow"))
        if self.vim_mode:
            segs.append((95, f"-- {self.vim_mode} --", ""))
        if self.permission_mode:
            color = _PERMISSION_COLORS.get(self.permission_mode, "")
            segs.append((90, self.permission_mode.upper(), f"bold {color}" if color else ""))
        if self.model:
            m = self.model[:20]
            segs.append((85, m, ""))
        if self.turn_count > 0:
            segs.append((70, f"turn {self.turn_count}", "dim"))
        if self.cwd_basename:
            segs.append((60, self.cwd_basename, "dim"))
        if self.git_branch:
            segs.append((65, f"[{self.git_branch}]", "magenta"))
        if self.tokens > 0:
            segs.append((50, f"↓{self.tokens:,} tok", ""))
        if self.context_limit > 0:
            segs.append((55, f"ctx {int(self.context_pct())}%", context_meter_style(self.context_pct())))
        if self.is_local:
            segs.append((40, "free", "dim"))
        elif self.cost:
            segs.append((40, self.cost, ""))
        if self.bg_tasks > 0:
            segs.append((45, f"{self.bg_tasks} task{'s' if self.bg_tasks > 1 else ''} running", "yellow"))
        if self.is_streaming:
            segs.append((80, "streaming…", "cyan"))
        if self.voice_elapsed > 0:
            # Format MM:SS (recordings shouldn't need hours).
            elapsed_int = int(self.voice_elapsed)
            mm, ss = divmod(elapsed_int, 60)
            segs.append((98, f"🎤 {mm:02d}:{ss:02d}", "bold red"))
        segs.append((30, "/help", "dim"))
        segs.append((20, "Ctrl+D quit", "dim"))
        return segs

    def _format_content(self) -> str:
        parts: list[str] = []
        if self.plan_mode:
            parts.append(self.plan_mode)
        if self.vim_mode:
            parts.append(f"-- {self.vim_mode} --")
        if self.model:
            parts.append(self.model)
        if self.tokens > 0:
            parts.append(f"↓{self.tokens:,} tok")
        if self.context_limit > 0:
            parts.append(f"ctx {int(self.context_pct())}%")
        if self.is_local:
            parts.append("free")
        elif self.cost:
            parts.append(self.cost)
        if self.bg_tasks > 0:
            parts.append(f"{self.bg_tasks} task{'s' if self.bg_tasks > 1 else ''} running")
        if self.is_streaming:
            parts.append("streaming…")
        if self.voice_elapsed > 0:
            elapsed_int = int(self.voice_elapsed)
            mm, ss = divmod(elapsed_int, 60)
            parts.append(f"🎤 {mm:02d}:{ss:02d}")
        parts.append("/help")
        parts.append("Ctrl+D quit")
        return " │ ".join(parts)

    def _fit_segments(self, width: int) -> list[tuple[int, str, str]]:
        segs = self._segments()
        # Try full list, then drop lowest-priority until it fits.
        def total_len(lst: list[tuple[int, str, str]]) -> int:
            return sum(len(s[1]) for s in lst) + max(0, len(lst) - 1) * 3
        if width <= 0:
            return segs
        while segs and total_len(segs) > width:
            # drop lowest-priority
            min_idx = min(range(len(segs)), key=lambda i: segs[i][0])
            segs.pop(min_idx)
        return segs

    def render(self) -> RenderResult:
        try:
            width = self.app.size.width if self.app else 0
        except Exception:
            width = 0
        segs = self._fit_segments(width)
        text = Text()
        for i, (_, s, style) in enumerate(segs):
            if i:
                text.append(" │ ", style="dim")
            text.append(s, style=style or "")
        return text

    def watch_model(self) -> None:
        self.refresh()

    def watch_tokens(self) -> None:
        self.refresh()

    def watch_cost(self) -> None:
        self.refresh()

    def watch_is_streaming(self) -> None:
        self.refresh()

    def watch_vim_mode(self) -> None:
        self.refresh()

    def watch_is_local(self) -> None:
        self.refresh()

    def watch_plan_mode(self) -> None:
        self.refresh()

    def watch_bg_tasks(self) -> None:
        self.refresh()

    def watch_voice_elapsed(self) -> None:
        self.refresh()

    def watch_context_used(self) -> None:
        self.refresh()

    def watch_context_limit(self) -> None:
        self.refresh()

    def watch_turn_count(self) -> None:
        self.refresh()

    def watch_permission_mode(self) -> None:
        self.refresh()

    def watch_cwd_basename(self) -> None:
        self.refresh()

    def watch_git_branch(self) -> None:
        self.refresh()
