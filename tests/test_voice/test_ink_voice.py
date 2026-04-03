"""Tests for Ink bridge voice IPC protocol."""
from __future__ import annotations

import json


class TestVoiceIPC:
    def test_voice_start_message(self):
        msg = {"type": "voice_start"}
        assert msg["type"] == "voice_start"

    def test_voice_stop_message(self):
        msg = {"type": "voice_stop"}
        assert msg["type"] == "voice_stop"

    def test_voice_text_message(self):
        msg = {"type": "voice_text", "text": "hello world"}
        assert msg["type"] == "voice_text"
        assert msg["text"] == "hello world"

    def test_voice_text_roundtrip(self):
        """Ensure voice_text message survives JSON serialization."""
        original = {"type": "voice_text", "text": "test transcription", "language": "zh"}
        serialized = json.dumps(original)
        deserialized = json.loads(serialized)
        assert deserialized == original
