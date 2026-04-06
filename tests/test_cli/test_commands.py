"""Tests for CLI slash command parsing and registry."""
from __future__ import annotations


from llm_code.cli.commands import (
    CommandDef,
    COMMAND_REGISTRY,
    KNOWN_COMMANDS,
    SlashCommand,
    parse_slash_command,
)


class TestCommandRegistry:
    """Tests for the single-source-of-truth command registry."""

    def test_registry_is_tuple_of_command_defs(self) -> None:
        assert isinstance(COMMAND_REGISTRY, tuple)
        for entry in COMMAND_REGISTRY:
            assert isinstance(entry, CommandDef)

    def test_known_commands_derived_from_registry(self) -> None:
        expected = frozenset(c.name for c in COMMAND_REGISTRY)
        assert KNOWN_COMMANDS == expected

    def test_no_duplicate_names(self) -> None:
        names = [c.name for c in COMMAND_REGISTRY]
        # "quit" is intentionally a duplicate of "exit", so exclude it
        non_alias = [n for n in names if n != "quit"]
        assert len(non_alias) == len(set(non_alias))

    def test_all_defs_frozen(self) -> None:
        for entry in COMMAND_REGISTRY:
            assert entry.__dataclass_params__.frozen  # type: ignore[attr-defined]

    def test_exit_and_quit_present(self) -> None:
        names = {c.name for c in COMMAND_REGISTRY}
        assert "exit" in names
        assert "quit" in names

    def test_no_arg_commands_have_flag(self) -> None:
        no_arg_names = {c.name for c in COMMAND_REGISTRY if c.no_arg}
        for name in ("help", "clear", "cost", "config", "vim", "skill",
                      "plugin", "mcp", "lsp", "cancel", "exit", "quit", "hida"):
            assert name in no_arg_names, f"{name} should be no_arg=True"

    def test_arg_commands_have_no_flag(self) -> None:
        arg_names = {c.name for c in COMMAND_REGISTRY if not c.no_arg}
        for name in ("model", "budget", "cd", "image", "search", "memory"):
            assert name in arg_names, f"{name} should be no_arg=False"


class TestParseSlashCommand:
    def test_help(self):
        cmd = parse_slash_command("/help")
        assert cmd == SlashCommand(name="help", args="")

    def test_clear(self):
        cmd = parse_slash_command("/clear")
        assert cmd == SlashCommand(name="clear", args="")

    def test_model_with_arg(self):
        cmd = parse_slash_command("/model qwen3:32b")
        assert cmd == SlashCommand(name="model", args="qwen3:32b")

    def test_session_subcommand(self):
        cmd = parse_slash_command("/session list")
        assert cmd == SlashCommand(name="session", args="list")

    def test_session_switch(self):
        cmd = parse_slash_command("/session switch abc123")
        assert cmd == SlashCommand(name="session", args="switch abc123")

    def test_config_set(self):
        cmd = parse_slash_command("/config set temperature 0.5")
        assert cmd == SlashCommand(name="config", args="set temperature 0.5")

    def test_cd(self):
        cmd = parse_slash_command("/cd /tmp")
        assert cmd == SlashCommand(name="cd", args="/tmp")

    def test_image(self):
        cmd = parse_slash_command("/image /path/to/screenshot.png")
        assert cmd == SlashCommand(name="image", args="/path/to/screenshot.png")

    def test_exit(self):
        cmd = parse_slash_command("/exit")
        assert cmd == SlashCommand(name="exit", args="")

    def test_cost(self):
        cmd = parse_slash_command("/cost")
        assert cmd == SlashCommand(name="cost", args="")

    def test_not_a_command(self):
        result = parse_slash_command("explain this code")
        assert result is None

    def test_empty_string(self):
        result = parse_slash_command("")
        assert result is None

    def test_whitespace_only(self):
        result = parse_slash_command("   ")
        assert result is None

    def test_leading_whitespace(self):
        cmd = parse_slash_command("  /help  ")
        assert cmd == SlashCommand(name="help", args="")

    def test_command_name_lowercased(self):
        cmd = parse_slash_command("/HELP")
        assert cmd is not None
        assert cmd.name == "help"

    def test_model_no_arg(self):
        cmd = parse_slash_command("/model")
        assert cmd == SlashCommand(name="model", args="")

    def test_slash_only(self):
        result = parse_slash_command("/")
        assert result is None


class TestKeybindCommand:
    def test_parse_keybind_no_args(self) -> None:
        from llm_code.cli.commands import parse_slash_command
        cmd = parse_slash_command("/keybind")
        assert cmd is not None
        assert cmd.name == "keybind"
        assert cmd.args == ""

    def test_parse_keybind_with_args(self) -> None:
        from llm_code.cli.commands import parse_slash_command
        cmd = parse_slash_command("/keybind submit ctrl+enter")
        assert cmd is not None
        assert cmd.name == "keybind"
        assert cmd.args == "submit ctrl+enter"

    def test_parse_keybind_reset(self) -> None:
        from llm_code.cli.commands import parse_slash_command
        cmd = parse_slash_command("/keybind reset")
        assert cmd is not None
        assert cmd.args == "reset"


class TestDiffCommand:
    def test_parse_diff_no_args(self) -> None:
        cmd = parse_slash_command("/diff")
        assert cmd is not None
        assert cmd.name == "diff"
        assert cmd.args == ""


class TestModeCommand:
    def test_parse_mode_no_args(self) -> None:
        cmd = parse_slash_command("/mode")
        assert cmd is not None
        assert cmd.name == "mode"
        assert cmd.args == ""

    def test_parse_mode_suggest(self) -> None:
        cmd = parse_slash_command("/mode suggest")
        assert cmd is not None
        assert cmd.name == "mode"
        assert cmd.args == "suggest"

    def test_parse_mode_normal(self) -> None:
        cmd = parse_slash_command("/mode normal")
        assert cmd is not None
        assert cmd.name == "mode"
        assert cmd.args == "normal"

    def test_parse_mode_plan(self) -> None:
        cmd = parse_slash_command("/mode plan")
        assert cmd is not None
        assert cmd.name == "mode"
        assert cmd.args == "plan"

    def test_mode_in_known_commands(self) -> None:
        from llm_code.cli.commands import KNOWN_COMMANDS
        assert "mode" in KNOWN_COMMANDS


class TestAuditCommand:
    def test_parse_audit_no_args(self) -> None:
        from llm_code.cli.commands import parse_slash_command
        cmd = parse_slash_command("/audit")
        assert cmd is not None
        assert cmd.name == "audit"
        assert cmd.args == ""

    def test_parse_audit_search(self) -> None:
        from llm_code.cli.commands import parse_slash_command
        cmd = parse_slash_command("/audit search bash")
        assert cmd is not None
        assert cmd.args == "search bash"
