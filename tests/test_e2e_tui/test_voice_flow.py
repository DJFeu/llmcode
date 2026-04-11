"""E2E: full `/voice` flow — hotkey toggle, typo reject, VAD auto-stop,
status-bar timer, transcription worker dispatched."""
from __future__ import annotations

from unittest.mock import MagicMock, patch


async def _install_mock_recorder(app, *, auto_stop_after: int | None = None):
    """Replace the AudioRecorder factory with a deterministic stub.

    When ``auto_stop_after`` is set, the mock recorder's
    ``should_auto_stop`` returns ``False`` for that many calls then
    flips to ``True`` — lets us simulate the VAD window without real
    audio capture timing.
    """
    rec = MagicMock()
    rec.start = MagicMock()
    rec.stop = MagicMock(return_value=b"\x00\x01" * 8000)  # 1s of fake PCM
    rec.elapsed_seconds = MagicMock(return_value=0.0)

    counter = {"n": 0}

    def _should_auto_stop():
        counter["n"] += 1
        if auto_stop_after is None:
            return False
        return counter["n"] > auto_stop_after

    rec.should_auto_stop = _should_auto_stop
    return rec


async def test_hotkey_starts_and_stops_recording(pilot_voice_app, monkeypatch):
    """Ctrl+G press 1 → `/voice on` runs (recorder.start called).
    Press 2 → `/voice off` runs (recorder.stop called, transcription
    worker dispatched)."""
    from llm_code.tui.input_bar import InputBar

    app, pilot = pilot_voice_app
    bar = app.query_one(InputBar)
    bar.focus()

    # Patch the voice module so AudioRecorder / STT are stubs.
    fake_rec = await _install_mock_recorder(app)
    fake_stt = MagicMock()
    fake_stt.transcribe.return_value = "mocked transcript"

    with patch(
        "llm_code.voice.recorder.AudioRecorder", return_value=fake_rec
    ), patch(
        "llm_code.voice.recorder.detect_backend", return_value=MagicMock()
    ), patch(
        "llm_code.voice.stt.create_stt_engine", return_value=fake_stt
    ):
        # Press the hotkey — first press starts.
        await pilot.press("ctrl+g")
        await pilot.pause()
        assert app._voice_active is True
        assert app._voice_recorder is fake_rec
        fake_rec.start.assert_called_once()

        # Press hotkey again — second press stops + dispatches STT.
        await pilot.press("ctrl+g")
        await pilot.pause()
        assert app._voice_active is False
        fake_rec.stop.assert_called_once()


async def test_voice_typo_does_not_silently_stop_recording(pilot_voice_app):
    """Typing `/voice /oof` while recording must not flip the recorder
    off — it's a typo and should surface a warning."""
    from llm_code.tui.input_bar import InputBar
    from llm_code.tui.chat_view import ChatScrollView

    app, pilot = pilot_voice_app
    bar = app.query_one(InputBar)
    bar.focus()

    # Pre-populate a recording state without going through the full
    # recorder init path — we're testing the dispatcher guard, not
    # the start-up flow.
    app._voice_active = True
    app._voice_recorder = MagicMock()
    app._voice_stt = MagicMock()

    # Submit /voice /oof via the dispatcher directly — bypasses the
    # InputBar character-at-a-time path but exercises the same branch.
    app._cmd_dispatcher.dispatch("voice", "/oof")
    await pilot.pause()

    # Recording must still be active.
    assert app._voice_active is True
    assert app._voice_recorder is not None
    # A chat entry explaining the typo should exist.
    from tests.test_e2e_tui.test_boot_banner import _rendered_text
    chat = app.query_one(ChatScrollView)
    rendered = _rendered_text(chat)
    assert "Unknown" in rendered and "/voice" in rendered
    assert "Still recording" in rendered


async def test_status_bar_voice_timer_appears_when_recording(pilot_voice_app):
    """Setting StatusBar.voice_elapsed > 0 should surface a 🎤 MM:SS
    segment; setting it to 0 should hide it."""
    from llm_code.tui.status_bar import StatusBar

    app, _pilot = pilot_voice_app
    status = app.query_one(StatusBar)

    # Idle state — no voice segment.
    rendered = status.render().plain if hasattr(status.render(), "plain") else str(status.render())
    assert "🎤" not in rendered

    # Recording state — simulate the voice monitor tick.
    status.voice_elapsed = 5.0
    rendered = status.render().plain if hasattr(status.render(), "plain") else str(status.render())
    assert "🎤" in rendered
    assert "00:05" in rendered

    # Back to idle — segment clears.
    status.voice_elapsed = 0.0
    rendered = status.render().plain if hasattr(status.render(), "plain") else str(status.render())
    assert "🎤" not in rendered


async def test_voice_monitor_fires_vad_auto_stop(pilot_voice_app):
    """When the recorder's ``should_auto_stop`` returns True, the
    next monitor tick should dispatch `/voice off` via the command
    dispatcher — the same code path as a manual stop."""
    from unittest.mock import MagicMock

    app, pilot = pilot_voice_app

    # Fake recorder that reports silence has elapsed.
    rec = MagicMock()
    rec.elapsed_seconds = MagicMock(return_value=3.0)
    rec.should_auto_stop = MagicMock(return_value=True)
    rec.stop = MagicMock(return_value=b"\x00\x01" * 8000)

    app._voice_recorder = rec
    app._voice_active = True
    app._voice_stt = MagicMock()
    app._voice_stt.transcribe.return_value = "silence-triggered"

    # Run one monitor tick. This should see auto_stop=True and
    # dispatch /voice off, which tears state down.
    app._tick_voice_monitor()
    await pilot.pause()

    # Dispatcher /voice off ran — recording torn down.
    assert app._voice_active is False
    rec.stop.assert_called_once()


async def test_no_recorder_monitor_is_safe(pilot_voice_app):
    """A tick with no recorder (voice never started, or monitor
    leftover from a previous session) must not raise."""
    app, _pilot = pilot_voice_app
    app._voice_recorder = None
    app._voice_active = False
    # Must not raise.
    app._tick_voice_monitor()


async def test_voice_off_with_no_speech_shows_mic_permission_hint(
    pilot_voice_app,
):
    """The `/voice off` handler should surface a detailed microphone-
    permission troubleshooting message when the recorder flags that
    it never heard any speech — the usual symptom of a denied macOS
    Microphone permission that leaves the callback receiving zero
    PCM bytes."""
    from unittest.mock import MagicMock

    from llm_code.tui.chat_view import ChatScrollView

    app, pilot = pilot_voice_app

    rec = MagicMock()
    rec.stop.return_value = b""  # empty buffer — same as mic-denied
    rec.stopped_no_speech = True
    rec._has_heard_speech = False
    rec._last_peak = 0
    rec._last_mean = 0.0

    app._voice_active = True
    app._voice_recorder = rec
    app._voice_stt = MagicMock()

    app._cmd_dispatcher.dispatch("voice", "off")
    await pilot.pause()

    # State flipped clean.
    assert app._voice_active is False
    # Chat should carry the troubleshooting text.
    from tests.test_e2e_tui.test_boot_banner import _rendered_text
    chat = app.query_one(ChatScrollView)
    rendered = _rendered_text(chat)
    assert "No audio captured" in rendered
    # The hint specifically names macOS Microphone settings so users
    # can copy-paste the steps.
    assert "macOS" in rendered
    assert "Microphone" in rendered
    # And it includes the peak telemetry so power users can see
    # whether any signal reached the recorder at all.
    assert "peak=0" in rendered


async def test_voice_off_with_speech_heard_runs_transcription(pilot_voice_app):
    """Happy path: when the recorder DID hear speech and returned a
    non-empty buffer, `/voice off` should dispatch the transcription
    worker — NOT show the mic-permission hint."""
    from unittest.mock import MagicMock

    from llm_code.tui.chat_view import ChatScrollView

    app, pilot = pilot_voice_app

    rec = MagicMock()
    rec.stop.return_value = b"\x00\x01" * 8000
    rec.stopped_no_speech = False
    rec._has_heard_speech = True
    rec._last_peak = 7500
    rec._last_mean = 4200.0

    app._voice_active = True
    app._voice_recorder = rec
    app._voice_stt = MagicMock()
    app._voice_stt.transcribe.return_value = "hello from e2e"

    # Close the coroutine so the event loop doesn't complain about
    # an awaited coroutine in test teardown.
    def _consume_coro(coro, *_, **__):
        coro.close()

    original_run_worker = app.run_worker

    def _tracked_run_worker(work, *args, **kwargs):
        if hasattr(work, "close"):
            work.close()
            return None
        return original_run_worker(work, *args, **kwargs)

    app.run_worker = _tracked_run_worker  # type: ignore[assignment]

    app._cmd_dispatcher.dispatch("voice", "off")
    await pilot.pause()

    # The transcription-worker path should have run, not the hint.
    from tests.test_e2e_tui.test_boot_banner import _rendered_text
    chat = app.query_one(ChatScrollView)
    rendered = _rendered_text(chat)
    assert "Transcribing" in rendered
    assert "No audio captured" not in rendered
