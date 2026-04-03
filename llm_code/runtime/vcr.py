"""VCR session recording and playback for llm-code."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


EVENT_TYPES = (
    "user_input",
    "llm_request",
    "llm_response",
    "tool_call",
    "tool_result",
    "stream_event",
    "error",
)


@dataclass(frozen=True)
class VCREvent:
    """A single recorded event with timestamp, type, and payload."""

    ts: float
    type: str
    data: dict


class VCRRecorder:
    """Records session events as JSONL lines to a file."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._closed = False
        self._file = None

    def _ensure_open(self) -> None:
        if self._file is None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._file = self._path.open("w", encoding="utf-8")

    def record(self, event_type: str, data: dict) -> None:
        """Write a single JSONL event line with the current timestamp."""
        if self._closed:
            raise RuntimeError("VCRRecorder is closed")
        self._ensure_open()
        entry = {"ts": time.time(), "type": event_type, "data": data}
        self._file.write(json.dumps(entry, ensure_ascii=False) + "\n")
        self._file.flush()

    def close(self) -> None:
        """Close the recording file."""
        if self._file is not None:
            self._file.close()
            self._file = None
        self._closed = True

    # Context manager support
    def __enter__(self) -> "VCRRecorder":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


class VCRPlayer:
    """Replays a JSONL recording file and provides summary statistics."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def _read_events(self) -> list[VCREvent]:
        """Parse all valid JSONL events from the file."""
        events: list[VCREvent] = []
        try:
            text = self._path.read_text(encoding="utf-8")
        except (FileNotFoundError, OSError):
            return events

        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                events.append(VCREvent(
                    ts=float(obj["ts"]),
                    type=str(obj["type"]),
                    data=obj.get("data", {}),
                ))
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                continue
        return events

    def replay(self, speed: float = 1.0) -> Iterator[VCREvent]:
        """Yield events from the recording, optionally with timing.

        Args:
            speed: Playback speed multiplier. 0.0 means instant (no sleep).
                   1.0 means real-time. 2.0 means double speed.
        """
        events = self._read_events()
        if not events:
            return

        prev_ts: float | None = None
        for event in events:
            if speed > 0.0 and prev_ts is not None:
                delay = (event.ts - prev_ts) / speed
                if delay > 0:
                    time.sleep(delay)
            prev_ts = event.ts
            yield event

    def summary(self) -> dict:
        """Return summary statistics for the recording.

        Returns:
            dict with keys:
              - event_count: total number of events
              - duration: wall-clock seconds from first to last event
              - tool_calls: dict mapping tool name to call count
        """
        events = self._read_events()
        if not events:
            return {"event_count": 0, "duration": 0.0, "tool_calls": {}}

        duration = events[-1].ts - events[0].ts if len(events) > 1 else 0.0

        tool_calls: dict[str, int] = {}
        for event in events:
            if event.type == "tool_call":
                name = event.data.get("name", "unknown")
                tool_calls[name] = tool_calls.get(name, 0) + 1

        return {
            "event_count": len(events),
            "duration": duration,
            "tool_calls": tool_calls,
        }
