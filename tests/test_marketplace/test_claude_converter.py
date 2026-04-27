"""Tests for the v16 M5 Claude Code plugin → llmcode manifest converter.

Three fixture plugins under ``tests/fixtures/plugins/claude/`` exercise:

* ``hello-world`` — minimal Claude plugin (name + version + description).
* ``full-featured`` — every supported Claude→llmcode mapping.
* ``edge-cases`` — out-of-coverage hook events, unknown top-level
  fields, malformed providesTools entries.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from llm_code.marketplace.converters.claude_plugin import (
    convert,
    convert_and_validate,
)
from llm_code.marketplace.manifest import (
    SUPPORTED_HOOK_EVENTS,
    parse_manifest_text,
)
from llm_code.marketplace.validator import ValidationError, validate


FIXTURE_ROOT = Path(__file__).parent.parent / "fixtures" / "plugins" / "claude"


# ---------------------------------------------------------------------------
# Hello-world: minimal plugin round-trips cleanly
# ---------------------------------------------------------------------------


class TestHelloWorld:
    def test_converts_with_no_warnings(self) -> None:
        plugin = FIXTURE_ROOT / "hello-world"
        text, warnings = convert(plugin)
        assert warnings == []
        assert "name = \"hello-world\"" in text
        assert "version = \"1.0.0\"" in text
        assert "description" in text

    def test_emitted_manifest_validates(self) -> None:
        plugin = FIXTURE_ROOT / "hello-world"
        text, _ = convert(plugin)
        m = parse_manifest_text(text)
        validate(m)
        assert m.name == "hello-world"


# ---------------------------------------------------------------------------
# Full-featured: every supported mapping fires
# ---------------------------------------------------------------------------


class TestFullFeatured:
    def test_emits_all_sections(self) -> None:
        plugin = FIXTURE_ROOT / "full-featured"
        text, warnings = convert(plugin)
        # Author (object form) collapses to "Name <email>".
        assert "Adam Hung <magic.music@gmail.com>" in text
        # Hooks
        assert "on_pre_tool_use" in text
        assert "scripts/lint.sh" in text
        assert "edit_*" in text
        assert "on_session_start" in text
        # MCP
        assert "[[mcp]]" in text
        assert "filesystem" in text
        assert "@modelcontextprotocol/server-filesystem" in text
        assert "weather-mcp" in text
        # Commands
        assert "[[commands]]" in text
        assert "review" in text
        assert "{{git_diff}}" in text
        assert "lint" in text
        # Themes
        assert "[themes.neon]" in text
        # Variables
        assert "[variables]" in text
        assert "PROJECT" in text
        # providesTools
        assert "providesTools" in text
        assert "fixture_pkg.tools:GreeterTool" in text
        # Permissions
        assert "[permissions]" in text
        assert "network = true" in text
        assert "fs_write = false" in text
        # No warnings expected for a fully-supported plugin.
        assert warnings == []

    def test_validates(self) -> None:
        plugin = FIXTURE_ROOT / "full-featured"
        manifest, warnings = convert_and_validate(plugin)
        assert warnings == []
        assert manifest.name == "full-featured"
        assert manifest.version == "2.1.0"
        # Hooks normalise to llmcode events.
        events = {h.event for h in manifest.hooks}
        assert events == {"on_pre_tool_use", "on_session_start"}
        # All event names land inside the supported set.
        assert events.issubset(set(SUPPORTED_HOOK_EVENTS))
        # MCP servers preserved.
        assert {m.name for m in manifest.mcp} == {"filesystem", "weather"}
        assert manifest.mcp[0].args == (
            "-y",
            "@modelcontextprotocol/server-filesystem",
            "/tmp",
        )
        # Commands preserved (prompt-only Claude shape becomes
        # prompt_template).
        cmd_names = {c.name for c in manifest.commands}
        assert cmd_names == {"review", "lint"}
        review = next(c for c in manifest.commands if c.name == "review")
        assert "git_diff" in review.prompt_template
        # providesTools preserved.
        assert manifest.provides_tools == (
            "fixture_pkg.tools:GreeterTool",
            "fixture_pkg.tools:CounterTool",
        )
        # Permissions preserved.
        assert manifest.permissions_dict() == {"network": True, "fs_write": False}


# ---------------------------------------------------------------------------
# Edge cases: warnings fire, supported subset still works
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def _convert(self) -> tuple[str, list[str]]:
        plugin = FIXTURE_ROOT / "edge-cases"
        return convert(plugin)

    def test_unsupported_hook_events_warn(self) -> None:
        _text, warnings = self._convert()
        # on_tab_complete + on_keystroke are out-of-coverage.
        assert any("on_tab_complete" in w for w in warnings)
        assert any("on_keystroke" in w for w in warnings)

    def test_unknown_top_level_field_warns(self) -> None:
        _text, warnings = self._convert()
        assert any("futureField" in w for w in warnings)

    def test_outputstyles_and_lspservers_warn(self) -> None:
        _text, warnings = self._convert()
        assert any("outputStyles" in w for w in warnings)
        assert any("lspServers" in w for w in warnings)

    def test_malformed_provides_tool_dropped_with_warning(self) -> None:
        text, warnings = self._convert()
        # Valid entry survives.
        assert "valid.module:Tool" in text
        # Malformed entry is dropped.
        assert "this is not formatted correctly" not in text
        # And we say so.
        assert any("not formatted correctly" in w or "providesTools" in w for w in warnings)

    def test_supported_hook_still_emitted(self) -> None:
        text, _warnings = self._convert()
        assert "on_pre_tool_use" in text
        assert "scripts/pre.sh" in text

    def test_emitted_text_validates(self) -> None:
        text, _warnings = self._convert()
        m = parse_manifest_text(text)
        validate(m)

    def test_agents_and_skills_dirs_advertise(self) -> None:
        _text, warnings = self._convert()
        # The converter says "agents/skills directories will be loaded
        # by the existing loaders" — informational, not error.
        assert any("agents" in w for w in warnings)
        assert any("skills" in w for w in warnings)


# ---------------------------------------------------------------------------
# Convert error paths
# ---------------------------------------------------------------------------


class TestConvertErrors:
    def test_missing_plugin_json(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            convert(tmp_path)

    def test_invalid_json(self, tmp_path: Path) -> None:
        plugin_dir = tmp_path / "broken"
        (plugin_dir / ".claude-plugin").mkdir(parents=True)
        (plugin_dir / ".claude-plugin" / "plugin.json").write_text("{not json")
        with pytest.raises(ValueError, match="not valid JSON"):
            convert(plugin_dir)

    def test_missing_name_field(self, tmp_path: Path) -> None:
        plugin_dir = tmp_path / "no-name"
        (plugin_dir / ".claude-plugin").mkdir(parents=True)
        (plugin_dir / ".claude-plugin" / "plugin.json").write_text(
            '{"version": "1.0.0"}'
        )
        with pytest.raises(ValueError, match="missing 'name'"):
            convert(plugin_dir)


# ---------------------------------------------------------------------------
# convert_and_validate raises on validator failures
# ---------------------------------------------------------------------------


class TestConvertAndValidateErrors:
    def test_invalid_emitted_manifest_raises_validation_error(
        self, tmp_path: Path,
    ) -> None:
        plugin = tmp_path / "bad-version"
        (plugin / ".claude-plugin").mkdir(parents=True)
        (plugin / ".claude-plugin" / "plugin.json").write_text(
            '{"name": "bad-ver", "version": "not-semver"}'
        )
        with pytest.raises(ValidationError, match="semver"):
            convert_and_validate(plugin)


# ---------------------------------------------------------------------------
# Hooks-from-file path
# ---------------------------------------------------------------------------


class TestHooksFromFile:
    def test_inlines_hooks_when_path(self, tmp_path: Path) -> None:
        plugin = tmp_path / "hooks-file"
        (plugin / ".claude-plugin").mkdir(parents=True)
        (plugin / ".claude-plugin" / "plugin.json").write_text(
            '{"name": "hf", "version": "1.0.0", "hooks": "hooks.json"}'
        )
        (plugin / "hooks.json").write_text(
            '{"hooks": {"on_pre_tool_use": [{"command": "x.sh"}]}}'
        )
        text, warnings = convert(plugin)
        assert "on_pre_tool_use" in text
        assert "x.sh" in text
        assert warnings == []

    def test_hooks_file_missing_warns(self, tmp_path: Path) -> None:
        plugin = tmp_path / "hooks-missing"
        (plugin / ".claude-plugin").mkdir(parents=True)
        (plugin / ".claude-plugin" / "plugin.json").write_text(
            '{"name": "hm", "version": "1.0.0", "hooks": "hooks.json"}'
        )
        text, warnings = convert(plugin)
        assert "not found" in " ".join(warnings)
        # Manifest still valid (no hooks section).
        m = parse_manifest_text(text)
        validate(m)
