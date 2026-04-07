"""Tests for the hierarchical status line: width gating, branch caching, perms colors."""
from __future__ import annotations

import time

from llm_code.tui import status_bar as sb
from llm_code.tui.status_bar import StatusBar, _PERMISSION_COLORS


def _new_bar() -> StatusBar:
    bar = StatusBar()
    # Bypass reactive descriptor magic by writing on instance __dict__
    return bar


def test_permission_color_mapping():
    assert _PERMISSION_COLORS["build"] == "blue"
    assert _PERMISSION_COLORS["plan"] == "yellow"
    assert _PERMISSION_COLORS["suggest"] == "cyan"
    assert _PERMISSION_COLORS["yolo"] == "red"


def test_segments_includes_branch_and_turn():
    bar = _new_bar()
    bar.model = "qwen3.5-coder"
    bar.turn_count = 7
    bar.git_branch = "main"
    bar.permission_mode = "build"
    segs = bar._segments()
    texts = [s[1] for s in segs]
    assert any("turn 7" == t for t in texts)
    assert any("[main]" == t for t in texts)
    assert any("BUILD" == t for t in texts)


def test_width_gating_drops_lowest_priority():
    bar = _new_bar()
    bar.model = "qwen3.5-coder"
    bar.turn_count = 7
    bar.git_branch = "main"
    bar.permission_mode = "build"
    bar.tokens = 12345
    full = bar._fit_segments(width=999)
    narrow = bar._fit_segments(width=20)
    assert len(narrow) < len(full)
    # Highest-priority items survive
    narrow_texts = [s[1] for s in narrow]
    # plan_mode/permission/model are highest priority — at least one survives
    assert any(t in ("BUILD", "qwen3.5-coder") for t in narrow_texts)


def test_branch_cache_ttl(monkeypatch, tmp_path):
    sb._branch_cache.clear()
    calls = {"n": 0}

    def fake_run(*a, **k):
        calls["n"] += 1
        class R:
            returncode = 0
            stdout = "main\n"
        return R()

    monkeypatch.setattr(sb.subprocess, "run", fake_run)
    sb._git_branch_or_worktree(tmp_path)
    sb._git_branch_or_worktree(tmp_path)  # cached
    assert calls["n"] <= 3  # 1st call: branch + 2 worktree probes; 2nd: 0
    # Force expiry
    sb._branch_cache[str(tmp_path)] = (time.monotonic() - sb._BRANCH_TTL - 1, "main")
    sb._git_branch_or_worktree(tmp_path)
    assert calls["n"] >= 4


def test_branch_empty_when_not_repo(monkeypatch, tmp_path):
    sb._branch_cache.clear()

    def fake_run(*a, **k):
        class R:
            returncode = 128
            stdout = ""
        return R()

    monkeypatch.setattr(sb.subprocess, "run", fake_run)
    assert sb._git_branch_or_worktree(tmp_path) == ""


def test_worktree_prefix(monkeypatch, tmp_path):
    sb._branch_cache.clear()
    seq = iter([
        ("main\n", 0),       # branch --show-current
        ("/tmp/repo/.git\n", 0),  # git-common-dir
        ("/tmp/repo/.git/worktrees/wt\n", 0),  # git-dir (different)
    ])

    def fake_run(*a, **k):
        out, rc = next(seq)
        class R:
            returncode = rc
            stdout = out
        return R()

    monkeypatch.setattr(sb.subprocess, "run", fake_run)
    result = sb._git_branch_or_worktree(tmp_path)
    assert result.startswith("worktree:")
