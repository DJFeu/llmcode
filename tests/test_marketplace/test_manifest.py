"""Tests for the v16 M5 plugin manifest schema + validator.

Covers parser behaviour (TOML shape, unknown sections, missing fields)
and validator behaviour (semver, hook event whitelist, name regex,
duplicate detection).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from llm_code.marketplace.manifest import (
    SUPPORTED_HOOK_EVENTS,
    HookSpec,
    MCPSpec,
    ManifestError,
    PluginManifest,
    load_manifest,
    parse_manifest_text,
)
from llm_code.marketplace.validator import ValidationError, validate


# ---------------------------------------------------------------------------
# parse_manifest_text — happy path
# ---------------------------------------------------------------------------


class TestParseHappyPath:
    def test_minimal_manifest_parses(self) -> None:
        text = """
[plugin]
name = "tiny"
version = "1.0.0"
"""
        m = parse_manifest_text(text)
        assert m.name == "tiny"
        assert m.version == "1.0.0"
        assert m.author == ""
        assert m.description == ""
        assert m.hooks == ()
        assert m.mcp == ()
        assert m.commands == ()

    def test_full_manifest_parses(self) -> None:
        text = """
[plugin]
name = "full"
version = "2.1.0"
author = "Adam Hung <magic@example.com>"
description = "Everything"
providesTools = ["pkg.tools:Greeter", "pkg.tools:Counter"]

[install]
subdir = "src/llmcode-bits"

[[hooks]]
event = "on_pre_tool_use"
command = "scripts/lint.sh"
tool_pattern = "edit_*"

[[hooks]]
event = "on_session_start"
command = "scripts/welcome.sh"

[[mcp]]
name = "filesystem"
command = "npx"
args = ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]

[[mcp]]
name = "weather"
command = "/usr/local/bin/weather-mcp"

[[commands]]
name = "review"
description = "Review staged diff"
prompt_template = "Review:\\n{{git_diff}}"

[themes.neon]
primary = "magenta"
accent = "cyan"

[variables]
PROJECT = "Example"
VERSION = "0.1"

[permissions]
network = true
fs_write = false
"""
        m = parse_manifest_text(text)
        assert m.name == "full"
        assert m.version == "2.1.0"
        assert m.author == "Adam Hung <magic@example.com>"
        assert m.subdir == "src/llmcode-bits"

        assert len(m.hooks) == 2
        assert m.hooks[0] == HookSpec(
            event="on_pre_tool_use",
            command="scripts/lint.sh",
            tool_pattern="edit_*",
        )
        assert m.hooks[1].tool_pattern is None

        assert len(m.mcp) == 2
        assert m.mcp[0] == MCPSpec(
            name="filesystem",
            command="npx",
            args=("-y", "@modelcontextprotocol/server-filesystem", "/tmp"),
        )
        assert m.mcp[1].args == ()

        assert len(m.commands) == 1
        assert m.commands[0].name == "review"
        assert "{{git_diff}}" in m.commands[0].prompt_template

        themes = m.themes_dict()
        assert themes == {"neon": {"primary": "magenta", "accent": "cyan"}}

        assert m.variables_dict() == {"PROJECT": "Example", "VERSION": "0.1"}

        assert m.provides_tools == ("pkg.tools:Greeter", "pkg.tools:Counter")
        assert m.permissions_dict() == {"network": True, "fs_write": False}

    def test_manifest_is_frozen(self) -> None:
        m = parse_manifest_text("[plugin]\nname = \"a\"\nversion = \"1.0.0\"\n")
        with pytest.raises(Exception):
            m.name = "mutated"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# parse_manifest_text — error paths
# ---------------------------------------------------------------------------


class TestParseErrors:
    def test_unknown_section_rejected(self) -> None:
        text = """
[plugin]
name = "x"
version = "1.0.0"

[unknown_section]
foo = "bar"
"""
        with pytest.raises(ManifestError, match="unknown section"):
            parse_manifest_text(text)

    def test_missing_plugin_section(self) -> None:
        text = '[install]\nsubdir = "x"\n'
        with pytest.raises(ManifestError, match=r"\[plugin\]"):
            parse_manifest_text(text)

    def test_missing_name(self) -> None:
        text = '[plugin]\nversion = "1.0.0"\n'
        with pytest.raises(ManifestError, match="name"):
            parse_manifest_text(text)

    def test_missing_version(self) -> None:
        text = '[plugin]\nname = "x"\n'
        with pytest.raises(ManifestError, match="version"):
            parse_manifest_text(text)

    def test_hook_missing_command(self) -> None:
        text = """
[plugin]
name = "x"
version = "1.0.0"

[[hooks]]
event = "on_pre_tool_use"
"""
        with pytest.raises(ManifestError, match="missing 'command'"):
            parse_manifest_text(text)

    def test_mcp_args_must_be_strings(self) -> None:
        text = """
[plugin]
name = "x"
version = "1.0.0"

[[mcp]]
name = "fs"
command = "npx"
args = [1, 2]
"""
        with pytest.raises(ManifestError, match="must be strings"):
            parse_manifest_text(text)

    def test_invalid_toml_raises(self) -> None:
        with pytest.raises(ManifestError, match="not valid TOML"):
            parse_manifest_text("[plugin\nname = \"x\"")


# ---------------------------------------------------------------------------
# load_manifest — disk-backed
# ---------------------------------------------------------------------------


class TestLoadManifest:
    def test_load_from_dir(self, tmp_path: Path) -> None:
        (tmp_path / "manifest.toml").write_text(
            '[plugin]\nname = "ok"\nversion = "1.2.3"\n'
        )
        m = load_manifest(tmp_path)
        assert m.name == "ok"
        assert m.version == "1.2.3"

    def test_load_from_file(self, tmp_path: Path) -> None:
        f = tmp_path / "manifest.toml"
        f.write_text('[plugin]\nname = "ok"\nversion = "1.2.3"\n')
        m = load_manifest(f)
        assert m.name == "ok"

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ManifestError, match="not found"):
            load_manifest(tmp_path / "manifest.toml")


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------


class TestValidator:
    def _ok(self) -> PluginManifest:
        return parse_manifest_text(
            '[plugin]\nname = "ok"\nversion = "1.0.0"\n'
        )

    def test_minimal_passes(self) -> None:
        validate(self._ok())  # no exception

    def test_bad_semver_rejected(self) -> None:
        m = parse_manifest_text(
            '[plugin]\nname = "x"\nversion = "not-semver"\n'
        )
        with pytest.raises(ValidationError, match="semver"):
            validate(m)

    def test_bad_name_rejected(self) -> None:
        m = parse_manifest_text(
            '[plugin]\nname = "1starts-with-digit"\nversion = "1.0.0"\n'
        )
        with pytest.raises(ValidationError, match="name"):
            validate(m)

    def test_unknown_hook_event_rejected(self) -> None:
        text = """
[plugin]
name = "x"
version = "1.0.0"

[[hooks]]
event = "on_random_event"
command = "x"
"""
        m = parse_manifest_text(text)
        with pytest.raises(ValidationError, match="not a supported event"):
            validate(m)

    def test_every_supported_hook_event_passes(self) -> None:
        for event in SUPPORTED_HOOK_EVENTS:
            text = f"""
[plugin]
name = "x"
version = "1.0.0"

[[hooks]]
event = "{event}"
command = "scripts/x.sh"
"""
            validate(parse_manifest_text(text))

    def test_shell_substitution_rejected(self) -> None:
        text = """
[plugin]
name = "x"
version = "1.0.0"

[[hooks]]
event = "on_pre_tool_use"
command = "echo $(whoami)"
"""
        m = parse_manifest_text(text)
        with pytest.raises(ValidationError, match="shell-substitution"):
            validate(m)

    def test_duplicate_mcp_rejected(self) -> None:
        text = """
[plugin]
name = "x"
version = "1.0.0"

[[mcp]]
name = "fs"
command = "x"

[[mcp]]
name = "fs"
command = "y"
"""
        m = parse_manifest_text(text)
        with pytest.raises(ValidationError, match="duplicate"):
            validate(m)

    def test_provides_tools_must_be_module_class(self) -> None:
        text = """
[plugin]
name = "x"
version = "1.0.0"
providesTools = ["just_a_name"]
"""
        m = parse_manifest_text(text)
        with pytest.raises(ValidationError, match="module.path:ClassName"):
            validate(m)

    def test_unknown_permission_key_rejected(self) -> None:
        text = """
[plugin]
name = "x"
version = "1.0.0"

[permissions]
something_random = true
"""
        m = parse_manifest_text(text)
        with pytest.raises(ValidationError, match="unknown permission key"):
            validate(m)

    def test_known_permissions_pass(self) -> None:
        text = """
[plugin]
name = "x"
version = "1.0.0"

[permissions]
network = true
fs_write = false
subprocess = false
env = false
"""
        validate(parse_manifest_text(text))


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------


class TestRoundTrip:
    def test_parse_then_validate_passes_for_full_manifest(self) -> None:
        text = """
[plugin]
name = "round"
version = "1.2.3"
author = "Adam"
providesTools = ["pkg.mod:Cls"]
[[hooks]]
event = "on_pre_tool_use"
command = "scripts/x.sh"
tool_pattern = "edit_*"

[[mcp]]
name = "fs"
command = "npx"
args = ["-y", "x"]

[[commands]]
name = "review"
description = "X"
prompt_template = "Review {{x}}"

[themes.dracula]
primary = "magenta"

[variables]
A = "1"

[permissions]
network = true
"""
        m = parse_manifest_text(text)
        validate(m)
