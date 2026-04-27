"""Dispatcher tests for /theme and /vim (v16 M4)."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional
from unittest.mock import MagicMock

import pytest

from llm_code.view.dispatcher import CommandDispatcher
from llm_code.view.stream_renderer import ViewStreamRenderer

from tests.test_view._stub_backend import StubRecordingBackend


def _make_state(
    tmp_path: Path,
    *,
    config: Any = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        cwd=tmp_path,
        budget=None,
        config=config,
        runtime=None,
        cost_tracker=None,
        skills=None,
        checkpoint_mgr=None,
        plan_mode=False,
        tool_reg=MagicMock(all_tools=lambda: []),
        input_tokens=0,
        output_tokens=0,
        last_stop_reason="unknown",
        context_warned=False,
    )


@pytest.fixture
def backend() -> StubRecordingBackend:
    return StubRecordingBackend()


@pytest.fixture
def dispatcher_factory(backend: StubRecordingBackend, tmp_path: Path):
    def _make(
        *,
        state: Optional[SimpleNamespace] = None,
    ) -> CommandDispatcher:
        if state is None:
            state = _make_state(tmp_path)
        renderer = ViewStreamRenderer(view=backend, state=state)
        return CommandDispatcher(view=backend, state=state, renderer=renderer)
    return _make


# ---------------------------------------------------------------------------
# /theme command
# ---------------------------------------------------------------------------


class TestThemeCommand:
    def test_no_arg_lists_themes(
        self, dispatcher_factory, backend: StubRecordingBackend,
    ) -> None:
        d = dispatcher_factory()
        d._cmd_theme("")
        info = "\n".join(backend.info_lines)
        assert "Themes:" in info
        for name in ("default", "dark", "dracula", "nord"):
            assert name in info

    def test_unknown_theme_prints_error(
        self, dispatcher_factory, backend: StubRecordingBackend,
    ) -> None:
        d = dispatcher_factory()
        d._cmd_theme("ghost")
        assert any(
            "Unknown theme" in m for m in backend.error_lines
        )

    def test_set_theme_swaps_palette(
        self, dispatcher_factory, backend: StubRecordingBackend, tmp_path: Path,
    ) -> None:
        from llm_code.view.repl import style as style_module
        from llm_code.view.repl.style import set_palette
        from llm_code.view.themes import get_theme

        # Snapshot for restore
        snapshot = style_module.palette
        try:
            cfg = SimpleNamespace(ui_theme="default")
            state = _make_state(tmp_path, config=cfg)
            d = dispatcher_factory(state=state)
            d._cmd_theme("dracula")
            expected = get_theme("dracula")
            assert expected is not None
            assert style_module.palette.user_prefix == expected.user_prefix
            assert state.config.ui_theme == "dracula"
        finally:
            set_palette(snapshot)


# ---------------------------------------------------------------------------
# /vim command
# ---------------------------------------------------------------------------


class TestVimCommand:
    def test_no_arg_shows_state(
        self, dispatcher_factory, backend: StubRecordingBackend, tmp_path: Path,
    ) -> None:
        cfg = SimpleNamespace(vim_mode=False)
        state = _make_state(tmp_path, config=cfg)
        d = dispatcher_factory(state=state)
        d._cmd_vim("")
        info = "\n".join(backend.info_lines)
        assert "off" in info.lower() or "Vim mode" in info

    def test_on_sets_true(
        self, dispatcher_factory, backend: StubRecordingBackend, tmp_path: Path,
    ) -> None:
        cfg = SimpleNamespace(vim_mode=False)
        state = _make_state(tmp_path, config=cfg)
        d = dispatcher_factory(state=state)
        d._cmd_vim("on")
        assert state.config.vim_mode is True

    def test_off_sets_false(
        self, dispatcher_factory, backend: StubRecordingBackend, tmp_path: Path,
    ) -> None:
        cfg = SimpleNamespace(vim_mode=True)
        state = _make_state(tmp_path, config=cfg)
        d = dispatcher_factory(state=state)
        d._cmd_vim("off")
        assert state.config.vim_mode is False

    def test_toggle_flips(
        self, dispatcher_factory, backend: StubRecordingBackend, tmp_path: Path,
    ) -> None:
        cfg = SimpleNamespace(vim_mode=False)
        state = _make_state(tmp_path, config=cfg)
        d = dispatcher_factory(state=state)
        d._cmd_vim("toggle")
        assert state.config.vim_mode is True
        d._cmd_vim("toggle")
        assert state.config.vim_mode is False

    def test_invalid_arg_errors(
        self, dispatcher_factory, backend: StubRecordingBackend, tmp_path: Path,
    ) -> None:
        cfg = SimpleNamespace(vim_mode=False)
        state = _make_state(tmp_path, config=cfg)
        d = dispatcher_factory(state=state)
        d._cmd_vim("garbage")
        assert any(
            "Usage" in m for m in backend.error_lines
        )
