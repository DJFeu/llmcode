"""Tests for compaction progress label."""
from __future__ import annotations

from llm_code.tui.compaction_label import CompactionProgress


def test_inactive_label_empty():
    p = CompactionProgress()
    assert p.label() == ""


def test_atomic_compaction():
    p = CompactionProgress()
    p.start()
    assert "Compacting context" in p.label()
    p.stop()
    assert p.label() == ""


def test_progress_label():
    p = CompactionProgress()
    p.update(5, 12)
    assert p.label() == "Compacting context: 5/12 messages"
