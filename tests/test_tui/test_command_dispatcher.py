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


def test_dispatcher_has_all_52_commands():
    """Every CommandDef in the registry must have a matching _cmd_ handler.

    The prior iteration hard-coded a name list and diverged from the
    live registry, which let four commands (update/theme/cache/personas)
    ship without an autocomplete hint. Deriving the expected names from
    ``COMMAND_REGISTRY`` keeps the two in lock-step automatically.
    """
    from llm_code.cli.commands import COMMAND_REGISTRY
    from llm_code.tui.command_dispatcher import CommandDispatcher

    app = MagicMock()
    dispatcher = CommandDispatcher(app)

    for cmd in COMMAND_REGISTRY:
        assert hasattr(dispatcher, f"_cmd_{cmd.name}"), (
            f"Missing _cmd_{cmd.name} — registry declares /{cmd.name} "
            f"but CommandDispatcher has no handler for it"
        )


def test_registry_has_no_dead_handlers():
    """Every _cmd_* on the dispatcher must be listed in COMMAND_REGISTRY.

    Prevents the opposite drift: a handler exists but has no prompt hint,
    so users only learn about it from the source code (as happened with
    /update, /theme, /cache, /personas before the 2026-04-11 audit).
    """
    from llm_code.cli.commands import COMMAND_REGISTRY
    from llm_code.tui.command_dispatcher import CommandDispatcher

    handler_names = {
        attr[len("_cmd_"):]
        for attr in dir(CommandDispatcher)
        if attr.startswith("_cmd_") and callable(getattr(CommandDispatcher, attr, None))
    }
    registry_names = {c.name for c in COMMAND_REGISTRY}

    # Every handler should have a hint.
    missing_hints = handler_names - registry_names
    assert not missing_hints, (
        f"Handlers without registry entries (no autocomplete hint): {missing_hints}"
    )


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


# ── /export tests ──────────────────────────────────────────────────────


def _make_export_session(messages):
    """Build a minimal Session stand-in for export rendering tests."""
    from types import SimpleNamespace
    from pathlib import Path

    return SimpleNamespace(
        id="abcd1234",
        name="",
        project_path=Path("/tmp/project"),
        created_at="2026-04-11T00:00:00+00:00",
        updated_at="2026-04-11T06:00:00+00:00",
        tags=(),
        messages=tuple(messages),
    )


def test_render_session_markdown_user_and_assistant_text():
    from llm_code.api.types import Message, TextBlock
    from llm_code.tui.command_dispatcher import _render_session_markdown

    session = _make_export_session([
        Message(role="user", content=(TextBlock(text="ping"),)),
        Message(role="assistant", content=(TextBlock(text="pong"),)),
    ])
    md = _render_session_markdown(session)

    assert "# Session abcd1234" in md
    assert "## 1. User" in md
    assert "ping" in md
    assert "## 2. Assistant" in md
    assert "pong" in md
    assert md.endswith("\n")


def test_render_session_markdown_includes_thinking_as_details():
    from llm_code.api.types import Message, TextBlock, ThinkingBlock
    from llm_code.tui.command_dispatcher import _render_session_markdown

    session = _make_export_session([
        Message(role="assistant", content=(
            ThinkingBlock(content="let me think"),
            TextBlock(text="done"),
        )),
    ])
    md = _render_session_markdown(session)

    assert "<details><summary>💭 thinking</summary>" in md
    assert "let me think" in md
    assert "done" in md


def test_render_session_markdown_tool_call_and_result():
    from llm_code.api.types import Message, ToolResultBlock, ToolUseBlock
    from llm_code.tui.command_dispatcher import _render_session_markdown

    session = _make_export_session([
        Message(role="assistant", content=(
            ToolUseBlock(id="t1", name="bash", input={"command": "ls"}),
        )),
        Message(role="user", content=(
            ToolResultBlock(tool_use_id="t1", content="file.py\n"),
        )),
    ])
    md = _render_session_markdown(session)

    assert "🔧 tool call" in md
    assert "`bash`" in md
    assert '"command": "ls"' in md
    assert "✅ tool result" in md
    assert "file.py" in md


def test_cmd_export_writes_markdown_to_requested_path(tmp_path):
    """`/export <path>` should write session markdown to the given file."""
    from llm_code.api.types import Message, TextBlock
    from llm_code.tui.command_dispatcher import CommandDispatcher

    app = MagicMock()
    app._cwd = str(tmp_path)
    app._runtime = MagicMock()
    app._runtime.session = _make_export_session([
        Message(role="user", content=(TextBlock(text="hi"),)),
        Message(role="assistant", content=(TextBlock(text="there"),)),
    ])

    dispatcher = CommandDispatcher(app)
    out = tmp_path / "conversation.md"
    dispatcher._cmd_export(str(out))

    assert out.exists()
    body = out.read_text(encoding="utf-8")
    assert "hi" in body
    assert "there" in body


def test_cmd_export_default_filename_in_cwd(tmp_path):
    """`/export` without args should create a timestamped file in the cwd."""
    from llm_code.api.types import Message, TextBlock
    from llm_code.tui.command_dispatcher import CommandDispatcher

    app = MagicMock()
    app._cwd = str(tmp_path)
    app._runtime = MagicMock()
    app._runtime.session = _make_export_session([
        Message(role="user", content=(TextBlock(text="hello"),)),
    ])

    dispatcher = CommandDispatcher(app)
    dispatcher._cmd_export("")

    files = list(tmp_path.glob("llmcode-export-abcd1234-*.md"))
    assert len(files) == 1
    assert "hello" in files[0].read_text(encoding="utf-8")


def test_cmd_export_empty_session_is_noop(tmp_path):
    """Exporting a session with no messages must not create a file."""
    from llm_code.tui.command_dispatcher import CommandDispatcher

    app = MagicMock()
    app._cwd = str(tmp_path)
    app._runtime = MagicMock()
    app._runtime.session = _make_export_session([])

    dispatcher = CommandDispatcher(app)
    dispatcher._cmd_export("")

    assert list(tmp_path.glob("*.md")) == []


def test_cmd_export_without_runtime_is_noop(tmp_path):
    from llm_code.tui.command_dispatcher import CommandDispatcher

    app = MagicMock()
    app._cwd = str(tmp_path)
    app._runtime = None

    dispatcher = CommandDispatcher(app)
    dispatcher._cmd_export("")
    assert list(tmp_path.glob("*.md")) == []


# ── /voice empty-audio test (moved below /export block) ────────────────


def test_cmd_voice_off_empty_audio_does_not_transcribe():
    from llm_code.tui.command_dispatcher import CommandDispatcher

    app = _make_voice_app()
    recorder = MagicMock()
    recorder.stop.return_value = b""
    app._voice_active = True
    app._voice_recorder = recorder
    app._voice_stt = MagicMock()

    dispatcher = CommandDispatcher(app)
    dispatcher._cmd_voice("off")

    assert app._voice_active is False
    app.run_worker.assert_not_called()
