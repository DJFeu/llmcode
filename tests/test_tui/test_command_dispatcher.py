"""Tests for the extracted CommandDispatcher."""
from unittest.mock import MagicMock, patch


def test_dispatcher_resolves_known_command():
    from llm_code.tui.command_dispatcher import CommandDispatcher
    app = MagicMock()
    dispatcher = CommandDispatcher(app)
    assert hasattr(dispatcher, "_cmd_help")


def test_dispatcher_returns_false_for_unknown():
    from llm_code.tui.command_dispatcher import CommandDispatcher
    app = MagicMock()
    dispatcher = CommandDispatcher(app)
    result = dispatcher.dispatch("nonexistent_xyz_cmd", "")
    assert result is False


def test_dispatcher_returns_true_for_known():
    from llm_code.tui.command_dispatcher import CommandDispatcher
    app = MagicMock()
    app._runtime = None
    app._config = MagicMock()
    app._skills = None
    dispatcher = CommandDispatcher(app)
    with patch.object(dispatcher, "_cmd_help"):
        result = dispatcher.dispatch("help", "")
    assert result is True


def test_dispatcher_has_all_51_commands():
    """Verify that all 51 expected _cmd_* methods exist on the dispatcher."""
    from llm_code.tui.command_dispatcher import CommandDispatcher
    app = MagicMock()
    dispatcher = CommandDispatcher(app)

    expected = [
        "compact", "exit", "quit", "help", "copy", "clear", "update",
        "theme", "model", "cost", "cache", "profile", "gain", "cd",
        "budget", "undo", "diff", "init", "index", "thinking", "vim",
        "image", "lsp", "cancel", "plan", "yolo", "mode", "harness",
        "knowledge", "dump", "analyze", "diff_check", "search", "set",
        "settings", "config", "session", "voice", "cron", "task",
        "personas", "orchestrate", "swarm", "vcr", "checkpoint",
        "memory", "map", "mcp", "ide", "hida", "skill", "plugin",
    ]

    for name in expected:
        assert hasattr(dispatcher, f"_cmd_{name}"), f"Missing _cmd_{name}"


def test_dispatch_calls_handler_with_args():
    """dispatch passes the args string through to the handler."""
    from llm_code.tui.command_dispatcher import CommandDispatcher
    app = MagicMock()
    dispatcher = CommandDispatcher(app)

    with patch.object(dispatcher, "_cmd_clear") as mock_clear:
        result = dispatcher.dispatch("clear", "some args")

    assert result is True
    mock_clear.assert_called_once_with("some args")


def test_quit_is_alias_for_exit():
    """_cmd_quit should be the same function as _cmd_exit."""
    from llm_code.tui.command_dispatcher import CommandDispatcher
    assert CommandDispatcher._cmd_quit is CommandDispatcher._cmd_exit


# ── Voice command wire-up tests ────────────────────────────────────────


def _make_voice_app(*, enabled: bool = True, backend: str = "whisper"):
    """Build a MagicMock app with a realistic VoiceConfig stub."""
    from llm_code.runtime.config_features import VoiceConfig

    app = MagicMock()
    app._voice_active = False
    app._voice_recorder = None
    app._voice_stt = None
    app._config = MagicMock()
    app._config.voice = VoiceConfig(enabled=enabled, backend=backend)
    return app


def test_cmd_voice_on_without_config_refuses():
    from llm_code.tui.command_dispatcher import CommandDispatcher

    app = _make_voice_app(enabled=False)
    dispatcher = CommandDispatcher(app)
    dispatcher._cmd_voice("on")

    # Stays inactive; no recorder created.
    assert app._voice_active is False
    assert app._voice_recorder is None


def test_cmd_voice_on_starts_recorder():
    """`/voice on` should detect a backend, start the recorder, and flip active."""
    from llm_code.tui.command_dispatcher import CommandDispatcher

    app = _make_voice_app()
    dispatcher = CommandDispatcher(app)

    with patch("llm_code.voice.recorder.AudioRecorder") as MockRecorder, \
         patch("llm_code.voice.recorder.detect_backend") as mock_detect, \
         patch("llm_code.voice.stt.create_stt_engine") as mock_create_stt:
        mock_detect.return_value = MagicMock()
        recorder_instance = MagicMock()
        MockRecorder.return_value = recorder_instance
        mock_create_stt.return_value = MagicMock()

        dispatcher._cmd_voice("on")

    assert app._voice_active is True
    assert app._voice_recorder is recorder_instance
    recorder_instance.start.assert_called_once()


def test_cmd_voice_off_transcribes_and_inserts():
    """`/voice off` should stop recorder, run STT, and schedule insertion."""
    from llm_code.tui.command_dispatcher import CommandDispatcher

    app = _make_voice_app()
    stt_engine = MagicMock()
    stt_engine.transcribe.return_value = "hello world"
    recorder = MagicMock()
    recorder.stop.return_value = b"\x00\x01" * 8000  # 1s of 16kHz 16-bit

    app._voice_active = True
    app._voice_recorder = recorder
    app._voice_stt = stt_engine

    # Close the coroutine handed to run_worker so the event loop doesn't warn.
    def _consume_coro(coro, *_, **__):
        coro.close()
    app.run_worker.side_effect = _consume_coro

    dispatcher = CommandDispatcher(app)
    dispatcher._cmd_voice("off")

    # State flipped immediately.
    assert app._voice_active is False
    assert app._voice_recorder is None
    recorder.stop.assert_called_once()
    # Transcription is scheduled via run_worker (async coroutine).
    app.run_worker.assert_called_once()


def test_cmd_voice_off_when_inactive_is_noop():
    """`/voice off` when no recording must not raise."""
    from llm_code.tui.command_dispatcher import CommandDispatcher

    app = _make_voice_app()
    dispatcher = CommandDispatcher(app)
    # Should not raise.
    dispatcher._cmd_voice("off")
    assert app._voice_active is False
    app.run_worker.assert_not_called()
