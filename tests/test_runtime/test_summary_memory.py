"""Tests for SummaryMemory in memory_layers.py."""
from __future__ import annotations

from pathlib import Path


from llm_code.runtime.memory_layers import LayeredMemory, SummaryMemory


class TestSummaryMemory:
    def test_save_and_load_summary(self, tmp_path):
        sm = SummaryMemory(memory_dir=tmp_path / "mem", project_path=Path("/proj"))
        sm.save_summary("abc123", "The user asked about sorting algorithms.", 4)
        result = sm.load_summary("abc123")
        assert result is not None
        assert "sorting algorithms" in result

    def test_load_missing_returns_none(self, tmp_path):
        sm = SummaryMemory(memory_dir=tmp_path / "mem", project_path=Path("/proj"))
        assert sm.load_summary("doesnotexist") is None

    def test_save_creates_file(self, tmp_path):
        sm = SummaryMemory(memory_dir=tmp_path / "mem", project_path=Path("/proj"))
        sm.save_summary("sess1", "Hello world.", 2)
        # There should be a .md file for the session
        files = list(sm._summaries_dir.glob("*.md"))
        assert len(files) == 1
        assert files[0].stem == "sess1"

    def test_list_summaries_empty(self, tmp_path):
        sm = SummaryMemory(memory_dir=tmp_path / "mem", project_path=Path("/proj"))
        assert sm.list_summaries() == []

    def test_list_summaries_returns_descriptors(self, tmp_path):
        sm = SummaryMemory(memory_dir=tmp_path / "mem", project_path=Path("/proj"))
        sm.save_summary("s1", "First summary line.\nMore text.", 3)
        sm.save_summary("s2", "Second session summary.", 7)
        entries = sm.list_summaries()
        assert len(entries) == 2
        ids = {e["id"] for e in entries}
        assert ids == {"s1", "s2"}

    def test_list_summaries_fields(self, tmp_path):
        sm = SummaryMemory(memory_dir=tmp_path / "mem", project_path=Path("/proj"))
        sm.save_summary("sid", "A one line summary.", 5)
        entries = sm.list_summaries()
        assert len(entries) == 1
        e = entries[0]
        assert e["id"] == "sid"
        assert e["message_count"] == 5
        assert e["first_line"] == "A one line summary."
        assert e["timestamp"] != ""

    def test_list_summaries_sorted_newest_first(self, tmp_path):
        import time

        sm = SummaryMemory(memory_dir=tmp_path / "mem", project_path=Path("/proj"))
        sm.save_summary("old", "Older summary.", 1)
        time.sleep(0.01)
        sm.save_summary("new", "Newer summary.", 2)
        entries = sm.list_summaries()
        assert entries[0]["id"] == "new"
        assert entries[1]["id"] == "old"

    def test_overwrite_summary(self, tmp_path):
        sm = SummaryMemory(memory_dir=tmp_path / "mem", project_path=Path("/proj"))
        sm.save_summary("s1", "First version.", 2)
        sm.save_summary("s1", "Updated version.", 4)
        result = sm.load_summary("s1")
        assert result == "Updated version."
        entries = sm.list_summaries()
        assert len(entries) == 1
        assert entries[0]["message_count"] == 4

    def test_different_projects_isolated(self, tmp_path):
        sm1 = SummaryMemory(memory_dir=tmp_path / "mem", project_path=Path("/proj/a"))
        sm2 = SummaryMemory(memory_dir=tmp_path / "mem", project_path=Path("/proj/b"))
        sm1.save_summary("shared_id", "Project A summary.", 2)
        assert sm2.load_summary("shared_id") is None

    def test_long_summary_body(self, tmp_path):
        sm = SummaryMemory(memory_dir=tmp_path / "mem", project_path=Path("/proj"))
        long_body = "Line of text.\n" * 100
        sm.save_summary("long_sess", long_body, 10)
        result = sm.load_summary("long_sess")
        assert result is not None
        assert "Line of text." in result

    def test_first_line_truncated_in_list(self, tmp_path):
        sm = SummaryMemory(memory_dir=tmp_path / "mem", project_path=Path("/proj"))
        long_first = "X" * 200
        sm.save_summary("trunc", long_first, 1)
        entries = sm.list_summaries()
        assert len(entries[0]["first_line"]) <= 120


class TestLayeredMemorySummaries:
    def test_summaries_property_available(self, tmp_path):
        lm = LayeredMemory(
            project_root=tmp_path,
            memory_dir=tmp_path / "mem",
            project_path=Path("/proj"),
        )
        assert lm.summaries is not None

    def test_save_and_load_via_layered_memory(self, tmp_path):
        lm = LayeredMemory(
            project_root=tmp_path,
            memory_dir=tmp_path / "mem",
            project_path=Path("/proj"),
        )
        lm.summaries.save_summary("sess_abc", "Discussed async patterns.", 6)
        result = lm.summaries.load_summary("sess_abc")
        assert result is not None
        assert "async patterns" in result

    def test_summaries_instance_is_summary_memory(self, tmp_path):
        lm = LayeredMemory(
            project_root=tmp_path,
            memory_dir=tmp_path / "mem",
            project_path=Path("/proj"),
        )
        assert isinstance(lm.summaries, SummaryMemory)
