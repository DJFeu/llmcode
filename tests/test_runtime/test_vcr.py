"""Tests for VCR session recording and playback."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from llm_code.runtime.vcr import VCREvent, VCRRecorder, VCRPlayer, EVENT_TYPES


# ---------------------------------------------------------------------------
# VCREvent tests
# ---------------------------------------------------------------------------

class TestVCREvent:
    def test_basic_creation(self):
        event = VCREvent(ts=1234567890.0, type="user_input", data={"text": "hello"})
        assert event.ts == 1234567890.0
        assert event.type == "user_input"
        assert event.data == {"text": "hello"}

    def test_frozen(self):
        event = VCREvent(ts=1.0, type="user_input", data={})
        with pytest.raises(Exception):
            event.ts = 2.0  # type: ignore[misc]

    def test_all_event_types_defined(self):
        expected = {
            "user_input", "llm_request", "llm_response",
            "tool_call", "tool_result", "stream_event", "error",
        }
        assert expected == set(EVENT_TYPES)


# ---------------------------------------------------------------------------
# VCRRecorder tests
# ---------------------------------------------------------------------------

class TestVCRRecorder:
    def test_creates_file_on_close(self, tmp_path: Path):
        path = tmp_path / "test.jsonl"
        recorder = VCRRecorder(path)
        recorder.record("user_input", {"text": "hello"})
        recorder.close()
        assert path.exists()

    def test_writes_jsonl_format(self, tmp_path: Path):
        path = tmp_path / "session.jsonl"
        recorder = VCRRecorder(path)
        recorder.record("user_input", {"text": "hi"})
        recorder.record("llm_response", {"text": "hello"})
        recorder.close()

        lines = path.read_text().strip().splitlines()
        assert len(lines) == 2
        obj = json.loads(lines[0])
        assert "ts" in obj
        assert obj["type"] == "user_input"
        assert obj["data"] == {"text": "hi"}

    def test_timestamp_is_float(self, tmp_path: Path):
        path = tmp_path / "session.jsonl"
        recorder = VCRRecorder(path)
        before = time.time()
        recorder.record("user_input", {"text": "hi"})
        after = time.time()
        recorder.close()

        lines = path.read_text().strip().splitlines()
        obj = json.loads(lines[0])
        assert isinstance(obj["ts"], float)
        assert before <= obj["ts"] <= after

    def test_creates_parent_dirs(self, tmp_path: Path):
        path = tmp_path / "recordings" / "session_001.jsonl"
        recorder = VCRRecorder(path)
        recorder.record("user_input", {"text": "hello"})
        recorder.close()
        assert path.exists()

    def test_context_manager(self, tmp_path: Path):
        path = tmp_path / "ctx.jsonl"
        with VCRRecorder(path) as recorder:
            recorder.record("user_input", {"text": "test"})
        assert path.exists()
        lines = path.read_text().strip().splitlines()
        assert len(lines) == 1

    def test_multiple_records_ordered(self, tmp_path: Path):
        path = tmp_path / "multi.jsonl"
        recorder = VCRRecorder(path)
        recorder.record("user_input", {"text": "a"})
        recorder.record("llm_request", {"model": "qwen"})
        recorder.record("tool_call", {"name": "bash"})
        recorder.close()

        lines = path.read_text().strip().splitlines()
        assert len(lines) == 3
        types = [json.loads(line)["type"] for line in lines]
        assert types == ["user_input", "llm_request", "tool_call"]

    def test_record_after_close_raises(self, tmp_path: Path):
        path = tmp_path / "closed.jsonl"
        recorder = VCRRecorder(path)
        recorder.close()
        with pytest.raises(Exception):
            recorder.record("user_input", {"text": "too late"})


# ---------------------------------------------------------------------------
# VCRPlayer tests
# ---------------------------------------------------------------------------

class TestVCRPlayer:
    def _write_events(self, path: Path, events: list[dict]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w") as f:
            for e in events:
                f.write(json.dumps(e) + "\n")

    def test_replay_yields_vcr_events(self, tmp_path: Path):
        path = tmp_path / "session.jsonl"
        self._write_events(path, [
            {"ts": 1000.0, "type": "user_input", "data": {"text": "hi"}},
            {"ts": 1001.0, "type": "llm_response", "data": {"text": "hello"}},
        ])
        player = VCRPlayer(path)
        events = list(player.replay(speed=0.0))
        assert len(events) == 2
        assert all(isinstance(e, VCREvent) for e in events)
        assert events[0].type == "user_input"
        assert events[1].type == "llm_response"

    def test_replay_preserves_data(self, tmp_path: Path):
        path = tmp_path / "session.jsonl"
        self._write_events(path, [
            {"ts": 1000.0, "type": "tool_call", "data": {"name": "bash", "cmd": "ls"}},
        ])
        player = VCRPlayer(path)
        events = list(player.replay(speed=0.0))
        assert events[0].data == {"name": "bash", "cmd": "ls"}

    def test_summary_event_count(self, tmp_path: Path):
        path = tmp_path / "session.jsonl"
        self._write_events(path, [
            {"ts": 1000.0, "type": "user_input", "data": {}},
            {"ts": 1001.0, "type": "llm_request", "data": {}},
            {"ts": 1002.0, "type": "tool_call", "data": {"name": "bash"}},
            {"ts": 1003.0, "type": "tool_result", "data": {}},
            {"ts": 1004.0, "type": "llm_response", "data": {}},
        ])
        player = VCRPlayer(path)
        summary = player.summary()
        assert summary["event_count"] == 5

    def test_summary_duration(self, tmp_path: Path):
        path = tmp_path / "session.jsonl"
        self._write_events(path, [
            {"ts": 1000.0, "type": "user_input", "data": {}},
            {"ts": 1010.0, "type": "llm_response", "data": {}},
        ])
        player = VCRPlayer(path)
        summary = player.summary()
        assert summary["duration"] == pytest.approx(10.0)

    def test_summary_tool_call_stats(self, tmp_path: Path):
        path = tmp_path / "session.jsonl"
        self._write_events(path, [
            {"ts": 1000.0, "type": "tool_call", "data": {"name": "bash"}},
            {"ts": 1001.0, "type": "tool_call", "data": {"name": "read_file"}},
            {"ts": 1002.0, "type": "tool_call", "data": {"name": "bash"}},
        ])
        player = VCRPlayer(path)
        summary = player.summary()
        assert summary["tool_calls"]["bash"] == 2
        assert summary["tool_calls"]["read_file"] == 1

    def test_summary_empty_file(self, tmp_path: Path):
        path = tmp_path / "empty.jsonl"
        path.write_text("")
        player = VCRPlayer(path)
        summary = player.summary()
        assert summary["event_count"] == 0
        assert summary["duration"] == 0.0
        assert summary["tool_calls"] == {}

    def test_replay_empty_file(self, tmp_path: Path):
        path = tmp_path / "empty.jsonl"
        path.write_text("")
        player = VCRPlayer(path)
        events = list(player.replay(speed=0.0))
        assert events == []

    def test_replay_skips_malformed_lines(self, tmp_path: Path):
        path = tmp_path / "mixed.jsonl"
        path.write_text(
            '{"ts": 1.0, "type": "user_input", "data": {}}\n'
            'not-json\n'
            '{"ts": 2.0, "type": "llm_response", "data": {}}\n'
        )
        player = VCRPlayer(path)
        events = list(player.replay(speed=0.0))
        assert len(events) == 2

    def test_summary_single_event_duration_zero(self, tmp_path: Path):
        path = tmp_path / "single.jsonl"
        self._write_events(path, [
            {"ts": 5000.0, "type": "user_input", "data": {}},
        ])
        player = VCRPlayer(path)
        summary = player.summary()
        assert summary["duration"] == 0.0


# ---------------------------------------------------------------------------
# Round-trip test (recorder → player)
# ---------------------------------------------------------------------------

class TestVCRRoundTrip:
    def test_record_then_replay(self, tmp_path: Path):
        path = tmp_path / "roundtrip.jsonl"

        with VCRRecorder(path) as recorder:
            recorder.record("user_input", {"text": "what is 2+2?"})
            recorder.record("llm_request", {"model": "qwen", "tokens": 10})
            recorder.record("tool_call", {"name": "bash", "cmd": "echo 4"})
            recorder.record("tool_result", {"output": "4"})
            recorder.record("llm_response", {"text": "The answer is 4."})

        player = VCRPlayer(path)
        events = list(player.replay(speed=0.0))
        assert len(events) == 5
        assert events[0].type == "user_input"
        assert events[0].data["text"] == "what is 2+2?"
        assert events[4].type == "llm_response"

        summary = player.summary()
        assert summary["event_count"] == 5
        assert summary["tool_calls"] == {"bash": 1}
