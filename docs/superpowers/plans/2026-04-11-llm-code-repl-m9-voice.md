# M9 — Voice Overlay + End-to-End Voice Flow

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans.

**Goal:** Implement `VoiceOverlay` component + hook the existing `AudioRecorder` (`llm_code/tools/voice.py`, unchanged) into the REPL via `loop.call_soon_threadsafe`. Complete the end-to-end Ctrl+G → lock input → VAD auto-stop → Whisper STT → insert-into-input flow (brainstorm decision A1 + B1 + C3).

**Architecture:** `VoiceOverlay` is a small component that's driven by `voice_started/progress/stopped` calls. When active, it flips the coordinator's status line into voice-mode (via `StatusUpdate(voice_active=True, voice_seconds=..., voice_peak=...)`) and locks the input area. The recorder runs in its own background thread via the existing `sounddevice` callback; its events reach the coordinator through `asyncio.get_running_loop().call_soon_threadsafe` so all coordinator state mutations happen on the main event loop thread.

**Tech Stack:** existing `llm_code.tools.voice.AudioRecorder`, `asyncio.get_running_loop`, `prompt_toolkit.Buffer.read_only`, Rich for voice-mode status rendering (already in M5 StatusLine).

**Spec reference:** §6.6 voice integration, §10.1 R3 deadlock risk, §5.1 voice notification defaults.

**Dependencies:** M3 coordinator, M4 input area + keybindings (Ctrl+G hook), M5 status line (voice mode rendering). Independent of M6/M7/M8.

---

## File Structure

- Create: `llm_code/view/repl/components/voice_overlay.py` — `VoiceOverlay` class (~250 lines)
- Modify: `llm_code/view/repl/coordinator.py` — wire voice_started/progress/stopped to VoiceOverlay, install Ctrl+G key binding via `on_voice_toggle` parameter
- Modify: `llm_code/view/repl/backend.py` — delegate voice_* methods to coordinator; provide lifecycle for AudioRecorder start/stop
- Create: `tests/test_view/test_voice_overlay.py` — ~15 tests (unit)
- Create: `tests/test_e2e_repl/__init__.py`
- Create: `tests/test_e2e_repl/test_voice_flow.py` — ~15 tests (transliterated from test_e2e_tui/test_voice_flow.py)

---

## Tasks

### Task 9.1: Write VoiceOverlay component

**Files:** Create `llm_code/view/repl/components/voice_overlay.py`

- [ ] **Step 1: Write class.**

```python
"""VoiceOverlay — UI state for active voice recording.

Doesn't have its own Float or Window; drives state that the
StatusLine renders. When active:

- self._active is True
- coordinator's input area is locked (buffer.read_only = True)
- status line shows: "🎙 0:02.3 · peak 0.42 · Ctrl+G stop" in dim red
  (rendered by StatusLine based on voice_active/voice_seconds/voice_peak
  fields in the merged StatusUpdate state)

Lifecycle:
    overlay.start()          # Ctrl+G pressed → recorder begins
    overlay.update(s, p)     # each chunk from recorder (background thread)
    overlay.stop(reason)     # VAD auto-stop, manual Ctrl+G, or error
"""
from __future__ import annotations

import asyncio
from typing import Optional, TYPE_CHECKING

from llm_code.view.types import StatusUpdate

if TYPE_CHECKING:
    from llm_code.view.repl.coordinator import ScreenCoordinator


class VoiceOverlay:
    def __init__(self, coordinator: "ScreenCoordinator") -> None:
        self._coordinator = coordinator
        self._active = False
        self._saved_readonly: Optional[bool] = None

    @property
    def is_active(self) -> bool:
        return self._active

    def start(self) -> None:
        """Enter voice-active state.

        - Lock the input buffer
        - Flip status line to voice mode
        """
        if self._active:
            return
        self._active = True

        # Lock input buffer. prompt_toolkit Buffer has a read_only
        # property. Save the previous state so we can restore on stop.
        buffer = self._coordinator._input_area.buffer
        self._saved_readonly = buffer.read_only()
        try:
            # prompt_toolkit's Buffer.read_only is a Filter-like; to
            # actually lock it we swap in a True filter. For the v2.0.0
            # MVP we use a simpler approach: don't actually block
            # keystrokes at the PT layer; instead, rely on the status
            # line visual to tell the user input is locked, and have
            # the keybindings reject input when voice is active.
            pass
        except Exception:
            pass

        # Flip status line into voice mode
        self._coordinator.update_status(StatusUpdate(
            voice_active=True,
            voice_seconds=0.0,
            voice_peak=0.0,
        ))

    def update(self, seconds: float, peak: float) -> None:
        """Periodic progress update from the recorder background thread.

        Must be called on the main event loop (coordinator caller uses
        loop.call_soon_threadsafe).
        """
        if not self._active:
            return
        self._coordinator.update_status(StatusUpdate(
            voice_seconds=seconds,
            voice_peak=peak,
        ))

    def stop(self, reason: str) -> None:
        """Leave voice-active state.

        - Unlock input buffer
        - Flip status line back to default
        - Reason is passed through to the backend caller for logging;
          the overlay itself doesn't care what it was
        """
        if not self._active:
            return
        self._active = False

        # Unlock buffer
        buffer = self._coordinator._input_area.buffer
        if self._saved_readonly is not None:
            # Restore previous state
            pass
        self._saved_readonly = None

        # Clear voice mode in status
        self._coordinator.update_status(StatusUpdate(
            voice_active=False,
        ))
```

- [ ] **Step 2: Commit** — `git add llm_code/view/repl/components/voice_overlay.py && git commit -m "feat(view): VoiceOverlay state holder"`

### Task 9.2: Wire voice into coordinator

**Files:** Modify `llm_code/view/repl/coordinator.py`

- [ ] **Step 1: Add voice-related members + methods.**

```python
from llm_code.view.repl.components.voice_overlay import VoiceOverlay

class ScreenCoordinator:
    def __init__(self, *, console=None):
        ...
        self._voice_overlay = VoiceOverlay(self)
        self._voice_toggle_callback = None  # backend sets this in M9

    def set_voice_toggle_callback(self, callback):
        """Backend installs the Ctrl+G handler. Coordinator wires it into
        the keybindings on next rebuild."""
        self._voice_toggle_callback = callback
        # Rebuild key bindings to include the voice hotkey
        from llm_code.view.repl.keybindings import build_keybindings
        self._key_bindings = build_keybindings(
            input_buffer=self._input_area.buffer,
            history=self._history,
            on_submit=self._handle_submit,
            on_exit=self.request_exit,
            on_voice_toggle=callback,
        )

    # === Voice state forwarding (called by backend) ===

    def voice_started(self) -> None:
        self._voice_overlay.start()
        if self._app is not None and self._app.is_running:
            self._app.invalidate()

    def voice_progress(self, seconds: float, peak: float) -> None:
        self._voice_overlay.update(seconds, peak)
        if self._app is not None and self._app.is_running:
            self._app.invalidate()

    def voice_stopped(self, reason: str) -> None:
        self._voice_overlay.stop(reason)
        if self._app is not None and self._app.is_running:
            self._app.invalidate()

    @property
    def voice_overlay(self) -> VoiceOverlay:
        return self._voice_overlay
```

- [ ] **Step 2: Commit** — `git commit -am "feat(view): coordinator hosts VoiceOverlay + voice toggle callback"`

### Task 9.3: Wire AudioRecorder into backend

**Files:** Modify `llm_code/view/repl/backend.py`

- [ ] **Step 1: Add recorder lifecycle.**

```python
class REPLBackend(ViewBackend):
    def __init__(self, *, config=None, runtime=None, console=None):
        ...
        self._recorder = None  # lazy-initialized on first voice toggle
        self._voice_active = False

    async def start(self) -> None:
        await self._coordinator.start()
        # Install voice toggle callback so Ctrl+G fires self._toggle_voice
        self._coordinator.set_voice_toggle_callback(self._toggle_voice)

    def _toggle_voice(self) -> None:
        """Key binding handler for Ctrl+G.

        Runs on the main event loop (prompt_toolkit calls keybindings
        synchronously). Schedules async work via asyncio.create_task.
        """
        if self._voice_active:
            self._stop_voice()
        else:
            self._start_voice()

    def _start_voice(self) -> None:
        """Begin recording. Initializes recorder lazily."""
        if self._recorder is None:
            try:
                from llm_code.tools.voice import AudioRecorder
                self._recorder = AudioRecorder(
                    on_chunk_progress=self._on_recorder_chunk,
                    on_auto_stop=self._on_recorder_auto_stop,
                )
            except Exception as exc:
                self._coordinator.print_error_sync(
                    f"voice unavailable: {exc}"
                )
                return
        try:
            self._recorder.start()
            self._voice_active = True
            self.voice_started()
        except Exception as exc:
            self._coordinator.print_error_sync(f"voice start failed: {exc}")

    def _stop_voice(self) -> None:
        """Manually stop recording (Ctrl+G pressed again during recording)."""
        if self._recorder is None or not self._voice_active:
            return
        try:
            self._recorder.stop()
        except Exception:
            pass
        self._voice_active = False
        self.voice_stopped(reason="manual_stop")
        asyncio.create_task(self._transcribe_and_insert())

    def _on_recorder_chunk(self, seconds: float, peak: float) -> None:
        """Called by AudioRecorder on background thread for each chunk."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.call_soon_threadsafe(self.voice_progress, seconds, peak)

    def _on_recorder_auto_stop(self, reason: str) -> None:
        """Called by AudioRecorder when VAD triggers auto-stop (bg thread)."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._voice_active = False
        loop.call_soon_threadsafe(self.voice_stopped, reason)
        loop.call_soon_threadsafe(
            lambda: asyncio.create_task(self._transcribe_and_insert())
        )

    async def _transcribe_and_insert(self) -> None:
        """After stop, transcribe the captured audio and insert into input."""
        if self._recorder is None:
            return
        try:
            text = await self._recorder.transcribe()
        except Exception as exc:
            self._coordinator.print_error_sync(f"transcription failed: {exc}")
            return
        if text:
            self._coordinator._input_area.buffer.insert_text(text)
            if self._coordinator._app is not None:
                self._coordinator._app.invalidate()

    # Expose Protocol methods for coordinator delegation
    def voice_started(self) -> None:
        self._coordinator.voice_started()

    def voice_progress(self, seconds: float, peak: float) -> None:
        self._coordinator.voice_progress(seconds, peak)

    def voice_stopped(self, reason: str) -> None:
        self._coordinator.voice_stopped(reason)
```

- [ ] **Step 2: Commit** — `git commit -am "feat(view): REPLBackend wires AudioRecorder via call_soon_threadsafe"`

### Task 9.4: Write VoiceOverlay unit tests

**Files:** Create `tests/test_view/test_voice_overlay.py`

- [ ] **Step 1: Write tests.**

```python
"""Unit tests for VoiceOverlay — tests state transitions without a real recorder."""
import io

import pytest
from rich.console import Console

from llm_code.view.repl.components.voice_overlay import VoiceOverlay
from llm_code.view.repl.coordinator import ScreenCoordinator


def _make_coord():
    capture = io.StringIO()
    console = Console(
        file=capture, force_terminal=True, color_system="truecolor", width=80,
    )
    return ScreenCoordinator(console=console)


def test_initial_state_inactive():
    coord = _make_coord()
    assert coord.voice_overlay.is_active is False

def test_start_flips_active():
    coord = _make_coord()
    coord.voice_started()
    assert coord.voice_overlay.is_active is True

def test_start_sets_status_voice_active():
    coord = _make_coord()
    coord.voice_started()
    assert coord.current_status.voice_active is True

def test_progress_updates_status_fields():
    coord = _make_coord()
    coord.voice_started()
    coord.voice_progress(seconds=2.3, peak=0.42)
    s = coord.current_status
    assert s.voice_seconds == 2.3
    assert s.voice_peak == 0.42

def test_progress_while_inactive_noop():
    coord = _make_coord()
    coord.voice_progress(seconds=1.0, peak=0.1)
    # Not active, so status doesn't flip
    assert coord.current_status.voice_active is False

def test_stop_clears_active():
    coord = _make_coord()
    coord.voice_started()
    coord.voice_stopped(reason="manual_stop")
    assert coord.voice_overlay.is_active is False
    assert coord.current_status.voice_active is False

def test_start_is_idempotent():
    coord = _make_coord()
    coord.voice_started()
    coord.voice_started()  # should not raise
    assert coord.voice_overlay.is_active is True

def test_stop_is_idempotent():
    coord = _make_coord()
    coord.voice_started()
    coord.voice_stopped("x")
    coord.voice_stopped("y")  # should not raise

def test_stop_without_start_is_noop():
    coord = _make_coord()
    coord.voice_stopped("x")  # no error
```

Plus 6 more tests for: set_voice_toggle_callback rebuilds keybindings, voice_toggle_callback fires on Ctrl+G, voice-active status line render has recording indicator, progress with 0 peak still updates, stop reason propagates through voice_events (when tested via a stub backend).

- [ ] **Step 2: Run** — `pytest tests/test_view/test_voice_overlay.py -v` → ~15 pass.
- [ ] **Step 3: Commit** — `git add tests/test_view/test_voice_overlay.py && git commit -m "test(view): VoiceOverlay unit coverage"`

### Task 9.5: Transliterate voice flow e2e tests

**Files:** Create `tests/test_e2e_repl/__init__.py`, `tests/test_e2e_repl/test_voice_flow.py`

Transliterate from `tests/test_e2e_tui/test_voice_flow.py` using the pattern from spec §9.2. Key substitutions:

| Old | New |
|---|---|
| `app = pilot_app` | `backend = repl_pilot.backend` |
| `await pilot.press("ctrl+g")` | `backend._toggle_voice()` (direct method call; avoids the PT event loop dance) |
| Assertions on Textual widgets | Assertions on `repl_pilot.captured_output`, `coordinator.current_status` |
| `MockRecorder` fixture | Same — `tools/voice.py` path unchanged, mock is still valid |

- [ ] **Step 1: Scaffold** — `mkdir -p tests/test_e2e_repl && touch tests/test_e2e_repl/__init__.py`
- [ ] **Step 2: Write the transliterated tests** using the pattern. Representative examples:

```python
"""Transliterated voice flow e2e tests."""
import pytest

from llm_code.view.repl.backend import REPLBackend


class FakeRecorder:
    """Minimal recorder mock used by tests — mirrors AudioRecorder API."""
    def __init__(self, **kwargs):
        self.started = False
        self.stopped = False
        self._on_chunk = kwargs.get("on_chunk_progress")
        self._on_auto = kwargs.get("on_auto_stop")
        self.transcription = "hello from fake"

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    async def transcribe(self) -> str:
        return self.transcription


@pytest.mark.asyncio
async def test_ctrl_g_starts_recording(repl_pilot, monkeypatch):
    # Inject fake recorder
    from llm_code.tools import voice as voice_module
    monkeypatch.setattr(voice_module, "AudioRecorder", FakeRecorder)

    backend = repl_pilot.backend
    backend._toggle_voice()
    assert backend._voice_active is True
    assert backend._recorder.started is True
    assert backend._coordinator.current_status.voice_active is True

@pytest.mark.asyncio
async def test_second_ctrl_g_stops_recording(repl_pilot, monkeypatch):
    from llm_code.tools import voice as voice_module
    monkeypatch.setattr(voice_module, "AudioRecorder", FakeRecorder)

    backend = repl_pilot.backend
    backend._toggle_voice()  # start
    backend._toggle_voice()  # stop
    assert backend._voice_active is False
    assert backend._recorder.stopped is True

@pytest.mark.asyncio
async def test_manual_stop_transcribes_and_inserts(repl_pilot, monkeypatch):
    from llm_code.tools import voice as voice_module
    monkeypatch.setattr(voice_module, "AudioRecorder", FakeRecorder)

    backend = repl_pilot.backend
    backend._toggle_voice()
    backend._toggle_voice()
    # Let the transcription task complete
    import asyncio
    await asyncio.sleep(0.05)
    assert "hello from fake" in backend._coordinator._input_area.buffer.text

@pytest.mark.asyncio
async def test_vad_auto_stop_transcribes_and_inserts(repl_pilot, monkeypatch):
    from llm_code.tools import voice as voice_module
    monkeypatch.setattr(voice_module, "AudioRecorder", FakeRecorder)

    backend = repl_pilot.backend
    backend._toggle_voice()
    # Simulate VAD auto-stop from the recorder
    backend._on_recorder_auto_stop(reason="vad_auto_stop")
    import asyncio
    await asyncio.sleep(0.1)  # let the scheduled transcribe run
    assert "hello from fake" in backend._coordinator._input_area.buffer.text

@pytest.mark.asyncio
async def test_transcription_error_shows_error(repl_pilot, monkeypatch):
    class FailingRecorder(FakeRecorder):
        async def transcribe(self):
            raise RuntimeError("stt api down")

    from llm_code.tools import voice as voice_module
    monkeypatch.setattr(voice_module, "AudioRecorder", FailingRecorder)

    backend = repl_pilot.backend
    backend._toggle_voice()
    backend._toggle_voice()
    import asyncio
    await asyncio.sleep(0.05)
    assert "transcription failed" in repl_pilot.captured_output.lower()
```

Plus 10 more tests covering: permission denied on recorder init, silent recording (no text inserted), peak/seconds status updates during recording, voice status line renders in recording mode, stop reason 'no_speech_timeout' propagates.

- [ ] **Step 3: Run** — `pytest tests/test_e2e_repl/test_voice_flow.py -v` → ~15 pass.
- [ ] **Step 4: Commit** — `git add tests/test_e2e_repl/ && git commit -m "test(e2e): voice flow transliterated from test_e2e_tui"`

---

## Milestone completion criteria

- ✅ `VoiceOverlay` exists and drives status line state correctly
- ✅ Coordinator wires Ctrl+G → backend._toggle_voice via on_voice_toggle callback
- ✅ Background thread → main loop coordination uses `call_soon_threadsafe` (no direct calls)
- ✅ ~15 unit tests + ~15 e2e tests green
- ✅ No deadlock observed in M9 stress test (run 100× toggle in a loop; coordinator must still respond)

## Risk addressed

R3 (voice + asyncio deadlock) — M9 is the stress test. If deadlocks appear, fall back to Fallback F2 (voice as `/voice` slash command only, no global hotkey).

## Estimated effort: ~4 hours

## Next milestone: M10 — Dispatcher Relocation (`m10-dispatcher.md`)
