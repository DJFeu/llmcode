"""Anthropic WebSocket STT backend."""
from __future__ import annotations

import asyncio
import base64
import json
import os


def _ws_transcribe(ws_url: str, audio_bytes: bytes, language: str) -> str:
    """Connect to Anthropic voice_stream WebSocket and transcribe."""
    return asyncio.get_event_loop().run_until_complete(
        _async_ws_transcribe(ws_url, audio_bytes, language)
    )


async def _async_ws_transcribe(ws_url: str, audio_bytes: bytes, language: str) -> str:
    """Async WebSocket transcription."""
    import websockets  # type: ignore[import]

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    url = f"{ws_url}/v1/voice_stream"

    async with websockets.connect(
        url,
        additional_headers={"x-api-key": api_key, "anthropic-version": "2024-01-01"},
    ) as ws:
        await ws.send(json.dumps({
            "type": "audio_start",
            "language": language,
            "encoding": "pcm_s16le",
            "sample_rate": 16000,
        }))

        # Send audio in chunks
        chunk_size = 32000  # 1 second of 16kHz 16-bit
        for i in range(0, len(audio_bytes), chunk_size):
            chunk = audio_bytes[i: i + chunk_size]
            await ws.send(json.dumps({
                "type": "audio_data",
                "data": base64.b64encode(chunk).decode(),
            }))

        await ws.send(json.dumps({"type": "audio_end"}))

        # Collect transcription
        transcript_parts: list[str] = []
        async for msg in ws:
            data = json.loads(msg)
            if data.get("type") == "transcription":
                transcript_parts.append(data.get("text", ""))
            elif data.get("type") == "transcription_complete":
                break

        return " ".join(transcript_parts).strip()


class AnthropicSTT:
    """Transcribe audio via Anthropic WebSocket voice_stream."""

    def __init__(self, ws_url: str = "wss://api.anthropic.com"):
        self._ws_url = ws_url

    def transcribe(self, audio_bytes: bytes, language: str) -> str:
        """Send audio to Anthropic WebSocket STT."""
        return _ws_transcribe(self._ws_url, audio_bytes, language)
