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
