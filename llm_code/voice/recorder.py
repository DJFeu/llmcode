"""Audio recording with sounddevice (primary) and sox/arecord fallback."""
from __future__ import annotations

import enum
import shutil
import subprocess
import threading
import time


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
        "Install sounddevice (`pip install llm-code[voice]`) or ensure sox/arecord is on PATH."
    )


class AudioRecorder:
    """Records 16kHz mono 16-bit PCM audio from the microphone."""

    def __init__(
        self,
        backend: RecorderBackend | None = None,
        sample_rate: int = 16000,
        channels: int = 1,
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

    def start(self) -> None:
        """Begin recording audio."""
        self._buffer = bytearray()
        self._recording = True
        self._start_time = time.monotonic()

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
        return result

    def elapsed_seconds(self) -> float:
        """Return seconds since recording started, or 0.0 if not recording."""
        if self._start_time is None or not self._recording:
            return 0.0
        return time.monotonic() - self._start_time

    def _start_sounddevice(self) -> None:
        import sounddevice as sd  # type: ignore[import]

        def callback(indata, frames, time_info, status):
            if self._recording:
                self._buffer.extend(indata.tobytes())

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

        self._thread = threading.Thread(target=_read_loop, daemon=True)
        self._thread.start()
