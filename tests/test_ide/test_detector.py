"""Tests for IDE process detection."""
from __future__ import annotations

import dataclasses
from unittest.mock import patch, MagicMock

import pytest

from llm_code.ide.detector import IDEInfo, detect_running_ide


class TestIDEInfo:
    def test_frozen(self):
        info = IDEInfo(name="vscode", pid=1234, workspace_path="/home/user/project")
        with pytest.raises(dataclasses.FrozenInstanceError):
            info.name = "other"  # type: ignore[misc]

    def test_fields(self):
        info = IDEInfo(name="neovim", pid=5678, workspace_path="/tmp/work")
        assert info.name == "neovim"
        assert info.pid == 5678
        assert info.workspace_path == "/tmp/work"


class TestDetectRunningIDE:
    def test_returns_empty_when_no_ide(self):
        with patch("llm_code.ide.detector._iter_processes", return_value=[]):
            result = detect_running_ide()
        assert result == []

    def test_detects_vscode(self):
        proc = MagicMock()
        proc.pid = 100
        proc.info = {"name": "code", "cmdline": ["code", "/home/user/project"]}
        with patch("llm_code.ide.detector._iter_processes", return_value=[proc]):
            result = detect_running_ide()
        assert len(result) == 1
        assert result[0].name == "vscode"
        assert result[0].pid == 100

    def test_detects_neovim(self):
        proc = MagicMock()
        proc.pid = 200
        proc.info = {"name": "nvim", "cmdline": ["nvim", "/tmp/code"]}
        with patch("llm_code.ide.detector._iter_processes", return_value=[proc]):
            result = detect_running_ide()
        assert len(result) == 1
        assert result[0].name == "neovim"

    def test_detects_jetbrains(self):
        proc = MagicMock()
        proc.pid = 300
        proc.info = {"name": "idea", "cmdline": ["idea", "/work"]}
        with patch("llm_code.ide.detector._iter_processes", return_value=[proc]):
            result = detect_running_ide()
        assert len(result) == 1
        assert result[0].name == "jetbrains"

    def test_detects_sublime(self):
        proc = MagicMock()
        proc.pid = 400
        proc.info = {"name": "subl", "cmdline": ["subl", "/code"]}
        with patch("llm_code.ide.detector._iter_processes", return_value=[proc]):
            result = detect_running_ide()
        assert len(result) == 1
        assert result[0].name == "sublime"

    def test_returns_empty_when_psutil_missing(self):
        with patch("llm_code.ide.detector._iter_processes", side_effect=ImportError):
            result = detect_running_ide()
        assert result == []

    def test_multiple_ides(self):
        proc_vs = MagicMock()
        proc_vs.pid = 100
        proc_vs.info = {"name": "code", "cmdline": ["code", "/a"]}
        proc_nv = MagicMock()
        proc_nv.pid = 200
        proc_nv.info = {"name": "nvim", "cmdline": ["nvim", "/b"]}
        with patch("llm_code.ide.detector._iter_processes", return_value=[proc_vs, proc_nv]):
            result = detect_running_ide()
        assert len(result) == 2
        names = {r.name for r in result}
        assert names == {"vscode", "neovim"}
