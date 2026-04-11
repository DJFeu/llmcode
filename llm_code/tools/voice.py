"""Voice input (STT) — consolidated module.

Phase 5.3 of the 2026-04-11 architecture refactor: the six tiny files
that used to live under ``llm_code/voice/`` (recorder, stt factory,
three STT backends, language table) are consolidated into a single
tools-layer module. The old package is kept as a shim so existing
imports (``from llm_code.voice.recorder import AudioRecorder``,
``from llm_code.voice.stt import create_stt_engine``) keep working and
so the large ``tests/test_voice/`` suite does not need to be rewritten.

The module is organized top-to-bottom in dependency order:

1. Languages — a plain data table used by STT backends.
2. ``AudioRecorder`` — sounddevice / sox / arecord capture.
3. ``STTEngine`` protocol + three backends (whisper, google, anthropic).
4. ``create_stt_engine(config)`` factory.

External dependencies (``sounddevice``, ``httpx``, ``google.cloud.speech``,
``websockets``) are all imported lazily inside the call sites that need
them, so importing this module is cheap even without the optional voice
extras installed.
"""
from __future__ import annotations

import asyncio
import base64
import enum
import json
import os
import shutil
import struct
import subprocess
import threading
import time
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from llm_code.runtime.config import VoiceConfig


# ── Languages ───────────────────────────────────────────────────────────

LANGUAGE_MAP: dict[str, str] = {
    "en": "English",
    "zh": "Chinese",
    "ja": "Japanese",
    "ko": "Korean",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "pt": "Portuguese",
    "ru": "Russian",
    "ar": "Arabic",
    "it": "Italian",
    "nl": "Dutch",
    "pl": "Polish",
    "sv": "Swedish",
    "da": "Danish",
    "no": "Norwegian",
    "fi": "Finnish",
    "tr": "Turkish",
    "th": "Thai",
    "vi": "Vietnamese",
    "id": "Indonesian",
    "ms": "Malay",
    "hi": "Hindi",
    "uk": "Ukrainian",
    "cs": "Czech",
    "el": "Greek",
    "he": "Hebrew",
    "hu": "Hungarian",
    "ro": "Romanian",
}


def validate_language(code: str) -> str:
    """Return the code if valid, raise ValueError otherwise."""
    if code not in LANGUAGE_MAP:
        raise ValueError(
            f"Unsupported language code: {code!r}. Valid: {sorted(LANGUAGE_MAP)}"
        )
    return code


# ── Audio recorder ──────────────────────────────────────────────────────


class RecorderBackend(enum.Enum):
    SOUNDDEVICE = "sounddevice"
    SOX = "sox"
    ARECORD = "arecord"


def detect_backend() -> RecorderBackend:
    """Detect the best available recording backend."""
    try:
        import sounddevice as _sd  # noqa: F401
        return RecorderBackend.SOUNDDEVICE
    except (ImportError, TypeError):
        pass

    if shutil.which("sox"):
        return RecorderBackend.SOX
    if shutil.which("arecord"):
        return RecorderBackend.ARECORD

    raise RuntimeError(
        "No audio recording backend available. "
        "Install sounddevice (`pip install llmcode-cli[voice]`) or ensure sox/arecord is on PATH."
    )


class AudioRecorder:
    """Records 16kHz mono 16-bit PCM audio from the microphone.

    Supports optional voice-activity detection (VAD): when ``silence_
    seconds > 0``, the recorder tracks an RMS energy floor on each
    incoming audio chunk and flips :attr:`is_silent` to ``True`` after
    that many seconds of sustained silence. Callers can poll
    :meth:`should_auto_stop` from a UI timer and tear the capture
    down automatically — no per-chunk callback plumbing required.
    """

    # Threshold chosen for 16-bit PCM microphone input. 500 is roughly
    # "very quiet room" — a real voice pushes this into the thousands
    # on the first vowel. Tune via the ``silence_threshold`` ctor arg
    # if your environment is noisy.
    _DEFAULT_SILENCE_THRESHOLD = 500

    def __init__(
        self,
        backend: RecorderBackend | None = None,
        sample_rate: int = 16000,
        channels: int = 1,
        *,
        silence_seconds: float = 0.0,
        silence_threshold: int = _DEFAULT_SILENCE_THRESHOLD,
    ):
        self._backend = backend or RecorderBackend.SOUNDDEVICE
        self.sample_rate = sample_rate
        self.channels = channels
        self._buffer = bytearray()
        self._recording = False
        self._start_time: float | None = None
        self._stream = None
        self._process: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        # VAD — 0 disables silence detection entirely.
        self._silence_seconds = silence_seconds
        self._silence_threshold = silence_threshold
        self._silence_start: float | None = None
        # Set by should_auto_stop() once the silence window is hit so
        # the TUI knows whether to stop on its own vs. at user command.
        self._auto_stopped = False

    def start(self) -> None:
        """Begin recording audio."""
        self._buffer = bytearray()
        self._recording = True
        self._start_time = time.monotonic()
        self._silence_start = None
        self._auto_stopped = False

        if self._backend == RecorderBackend.SOUNDDEVICE:
            self._start_sounddevice()
        elif self._backend == RecorderBackend.SOX:
            self._start_external([
                "sox", "-d", "-t", "raw", "-r", str(self.sample_rate),
                "-e", "signed", "-b", "16", "-c", str(self.channels), "-",
            ])
        elif self._backend == RecorderBackend.ARECORD:
            self._start_external([
                "arecord", "-f", "S16_LE", "-r", str(self.sample_rate),
                "-c", str(self.channels), "-t", "raw", "-",
            ])

    def stop(self) -> bytes:
        """Stop recording and return the captured PCM bytes."""
        if not self._recording:
            return b""

        self._recording = False

        if self._backend == RecorderBackend.SOUNDDEVICE and self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        elif self._process is not None:
            self._process.terminate()
            self._process.wait(timeout=3)
            self._process = None
        if self._thread is not None:
            self._thread.join(timeout=3)
            self._thread = None

        result = bytes(self._buffer)
        self._buffer = bytearray()
        self._start_time = None
        self._silence_start = None
        return result

    def elapsed_seconds(self) -> float:
        """Return seconds since recording started, or 0.0 if not recording."""
        if self._start_time is None or not self._recording:
            return 0.0
        return time.monotonic() - self._start_time

    def should_auto_stop(self) -> bool:
        """Return ``True`` when VAD says the speaker has gone quiet.

        Poll this from a UI timer (~every 200 ms is fine) to drive the
        "stop automatically after N seconds of silence" flow. Returns
        ``False`` when VAD is disabled (``silence_seconds == 0``) or
        no silence window has accumulated yet. Once it returns
        ``True``, subsequent calls keep returning ``True`` until the
        recorder is stopped — no flip-flopping.
        """
        if self._silence_seconds <= 0.0 or not self._recording:
            return False
        if self._silence_start is None:
            return False
        elapsed = time.monotonic() - self._silence_start
        if elapsed >= self._silence_seconds:
            self._auto_stopped = True
            return True
        return False

    @property
    def auto_stopped(self) -> bool:
        """True if the last stop was triggered by VAD, not by the caller."""
        return self._auto_stopped

    def _update_silence_tracker(self, chunk: bytes) -> None:
        """Internal: update the silence timer from a freshly-read PCM chunk.

        Computes the chunk's mean absolute sample value (a cheap RMS
        proxy — good enough for "is there anyone talking" and avoids
        a numpy dependency). Values below ``_silence_threshold`` start
        / continue a silence window; values above reset it.
        """
        if self._silence_seconds <= 0.0 or not chunk:
            return
        # Interpret as int16 little-endian. `array` beats struct for
        # big chunks because it does the unpacking in C.
        import array
        samples = array.array("h")
        # Discard trailing half-sample if the chunk length is odd.
        usable = len(chunk) - (len(chunk) & 1)
        samples.frombytes(chunk[:usable])
        if not samples:
            return
        mean_abs = sum(abs(s) for s in samples) / len(samples)
        now = time.monotonic()
        if mean_abs < self._silence_threshold:
            if self._silence_start is None:
                self._silence_start = now
        else:
            self._silence_start = None

    def _start_sounddevice(self) -> None:
        import sounddevice as sd  # type: ignore[import]

        def callback(indata, frames, time_info, status):
            if self._recording:
                chunk = indata.tobytes()
                self._buffer.extend(chunk)
                self._update_silence_tracker(chunk)

        self._stream = sd.RawInputStream(
            samplerate=self.sample_rate,
            channels=self.channels,
            dtype="int16",
            callback=callback,
        )
        self._stream.start()

    def _start_external(self, cmd: list[str]) -> None:
        self._process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )

        def _read_loop():
            assert self._process is not None
            assert self._process.stdout is not None
            while self._recording:
                chunk = self._process.stdout.read(4096)
                if not chunk:
                    break
                self._buffer.extend(chunk)
                self._update_silence_tracker(chunk)

        self._thread = threading.Thread(target=_read_loop, daemon=True)
        self._thread.start()


# ── STT protocol ────────────────────────────────────────────────────────


@runtime_checkable
class STTEngine(Protocol):
    """Protocol for speech-to-text backends."""

    def transcribe(self, audio_bytes: bytes, language: str) -> str:
        """Transcribe raw PCM audio bytes to text."""
        ...


# ── Whisper backend ─────────────────────────────────────────────────────


def _pcm_to_wav(
    pcm: bytes,
    sample_rate: int = 16000,
    channels: int = 1,
    sample_width: int = 2,
) -> bytes:
    """Wrap raw PCM bytes in a WAV header."""
    data_size = len(pcm)
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + data_size,
        b"WAVE",
        b"fmt ",
        16,
        1,  # PCM
        channels,
        sample_rate,
        sample_rate * channels * sample_width,
        channels * sample_width,
        sample_width * 8,
        b"data",
        data_size,
    )
    return header + pcm


class WhisperSTT:
    """Transcribe audio via an OpenAI-compatible Whisper endpoint."""

    def __init__(self, url: str = "http://localhost:8000/v1/audio/transcriptions"):
        self._url = url

    def transcribe(self, audio_bytes: bytes, language: str) -> str:
        """Send PCM audio as WAV to the Whisper endpoint."""
        import httpx

        wav_data = _pcm_to_wav(audio_bytes)
        response = httpx.post(
            self._url,
            files={"file": ("audio.wav", wav_data, "audio/wav")},
            data={"language": language, "response_format": "json"},
            timeout=30.0,
        )
        response.raise_for_status()
        return response.json().get("text", "")


# ── Google Cloud Speech backend ─────────────────────────────────────────


def _get_google_client():
    """Lazy import and create Google Speech client."""
    from google.cloud import speech  # type: ignore[import]
    return speech.SpeechClient()


class GoogleSTT:
    """Transcribe audio via Google Cloud Speech-to-Text API."""

    def __init__(self, language_code: str = "en-US"):
        self._language_code = language_code

    def transcribe(self, audio_bytes: bytes, language: str) -> str:
        """Send PCM audio to Google Cloud Speech."""
        from google.cloud import speech  # type: ignore[import]

        client = _get_google_client()
        audio = speech.RecognitionAudio(content=audio_bytes)
        config = speech.RecognitionConfig(
            encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
            sample_rate_hertz=16000,
            language_code=self._language_code if self._language_code else language,
        )
        response = client.recognize(config=config, audio=audio)

        if not response.results:
            return ""
        return response.results[0].alternatives[0].transcript


# ── Anthropic WebSocket backend ─────────────────────────────────────────


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


# ── Local Whisper backend ───────────────────────────────────────────────


class LocalWhisperSTT:
    """Embedded Whisper inference via ``faster-whisper`` — no HTTP server.

    Chosen as the "batteries-included" backend: users who don't want to
    stand up a separate `whisper-asr-webservice` container can
    ``pip install llmcode-cli[voice-local]`` and have an on-device STT
    that runs against one of the standard Whisper model sizes
    (``tiny`` / ``base`` / ``small`` / ``medium`` / ``large-v3``).

    Inference is lazy: the model is not loaded until the first
    ``transcribe`` call, so merely constructing this class has zero
    cost — important because the factory may build it eagerly on
    ``/voice on`` while the user is just opening the recording.

    The model and its downloaded weights are cached by faster-whisper
    inside ``~/.cache/huggingface/hub/`` (per their defaults).
    """

    def __init__(
        self,
        model_size: str = "base",
        device: str = "auto",
        compute_type: str = "default",
    ) -> None:
        self._model_size = model_size
        self._device = device
        self._compute_type = compute_type
        # Lazy: faster_whisper.WhisperModel or None until first transcribe.
        self._model = None

    def transcribe(self, audio_bytes: bytes, language: str) -> str:
        if self._model is None:
            try:
                from faster_whisper import WhisperModel  # type: ignore[import-not-found]
            except ImportError as exc:
                raise RuntimeError(
                    "faster-whisper is not installed. Run "
                    "`pip install llmcode-cli[voice-local]` to enable the "
                    "local Whisper backend."
                ) from exc
            self._model = WhisperModel(
                self._model_size,
                device=self._device,
                compute_type=self._compute_type,
            )

        # faster-whisper accepts a file path OR a numpy array of float32
        # samples. We already hold raw PCM bytes, so the cheapest path is
        # to wrap them as a WAV in a temp file — avoids a numpy dep on
        # the import path for callers that don't use this backend.
        import tempfile

        wav_data = _pcm_to_wav(audio_bytes)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(wav_data)
            tmp_path = f.name

        try:
            segments, _info = self._model.transcribe(
                tmp_path, language=language or None
            )
            return " ".join(seg.text.strip() for seg in segments).strip()
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


# ── Factory ─────────────────────────────────────────────────────────────


def create_stt_engine(config: "VoiceConfig") -> STTEngine:
    """Factory: create an STT engine from config.

    Supported backends:

    * ``"local"`` — embedded faster-whisper (no server; downloads model
      on first use). ``config.local_model`` selects the model size.
    * ``"whisper"`` — HTTP POST to an OpenAI-compatible whisper endpoint
      (e.g. ``whisper-asr-webservice``). ``config.whisper_url`` picks
      the endpoint.
    * ``"google"`` — Google Cloud Speech-to-Text.
    * ``"anthropic"`` — Anthropic WebSocket voice_stream.
    """
    backend = config.backend

    if backend == "local":
        return LocalWhisperSTT(
            model_size=getattr(config, "local_model", "base") or "base",
        )
    if backend == "whisper":
        return WhisperSTT(url=config.whisper_url)
    if backend == "google":
        return GoogleSTT(language_code=config.google_language_code or config.language)
    if backend == "anthropic":
        return AnthropicSTT(ws_url=config.anthropic_ws_url)

    raise ValueError(
        f"Unknown STT backend: {backend!r}. "
        "Valid: local, whisper, google, anthropic"
    )


__all__ = [
    "LANGUAGE_MAP",
    "AnthropicSTT",
    "AudioRecorder",
    "GoogleSTT",
    "LocalWhisperSTT",
    "RecorderBackend",
    "STTEngine",
    "WhisperSTT",
    "create_stt_engine",
    "detect_backend",
    "validate_language",
]
