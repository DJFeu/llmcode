"""Tests for M15 Group F help/marketplace/token warning/design system."""
from __future__ import annotations

from rich.console import Console

from llm_code.view.repl.components.design_system import (
    divider,
    keyboard_hint,
    loading_state,
    progress_bar,
    status_icon,
)
from llm_code.view.repl.components.help_table import render_help
from llm_code.view.repl.components.marketplace import (
    MarketplaceEntry,
    render_entry_list,
    render_entry_metadata,
)
from llm_code.view.repl.components.token_warning import (
    render_token_warning,
    should_warn,
)


def _render(r) -> str:
    c = Console(width=100, record=True, color_system="truecolor")
    c.print(r)
    return c.export_text()


# === F1 Help table ===


def test_help_renders_category_panels() -> None:
    commands = [
        ("/help", "Show help"),
        ("/quit", "Exit the REPL"),
        ("/bash", "Run a shell command"),
        ("/plan", "Plan mode"),
        ("/skill", "Manage skills"),
        ("/swarm", "Launch multi-agent swarm"),
    ]
    out = _render(render_help(commands))
    assert "Core" in out
    assert "Mode" in out
    assert "Tools" in out
    assert "/help" in out
    assert "/bash" in out


def test_help_with_unknown_commands_falls_into_other() -> None:
    commands = [("/xyz", "an unknown command")]
    out = _render(render_help(commands))
    assert "Other" in out
    assert "/xyz" in out


# === F2 Marketplace ===


def test_marketplace_metadata_panel() -> None:
    entry = MarketplaceEntry(
        name="example-skill",
        version="1.0.0",
        description="An example skill",
        author="test",
    )
    out = _render(render_entry_metadata(entry))
    assert "example-skill" in out
    assert "1.0.0" in out
    assert "test" in out


def test_marketplace_entry_list() -> None:
    entries = [
        MarketplaceEntry(name="alpha", version="1.0", installed=True),
        MarketplaceEntry(name="beta", version="2.0", installed=False),
    ]
    out = _render(render_entry_list(entries))
    assert "alpha" in out
    assert "beta" in out


# === F3 Token warning ===


def test_token_warning_triggers_at_80_percent() -> None:
    assert should_warn(800, 1000)
    assert should_warn(1000, 1000)


def test_token_warning_does_not_trigger_below_80() -> None:
    assert not should_warn(700, 1000)


def test_token_warning_renders_percentage() -> None:
    out = _render(render_token_warning(900, 1000))
    assert "90%" in out
    assert "⚠" in out


# === F4 Design system ===


def test_divider_has_brand_color() -> None:
    div = divider()
    assert div is not None


def test_status_icon_success() -> None:
    out = _render(status_icon("success"))
    assert "✓" in out


def test_status_icon_failure() -> None:
    out = _render(status_icon("failure"))
    assert "✗" in out


def test_keyboard_hint_renders_keys_and_action() -> None:
    out = _render(keyboard_hint("Ctrl+O", "expand"))
    assert "Ctrl+O" in out
    assert "expand" in out


def test_loading_state() -> None:
    out = _render(loading_state("fetching skills"))
    assert "fetching skills" in out


def test_progress_bar_fills_proportionally() -> None:
    bar = progress_bar(0.5, width=10)
    out = _render(bar)
    assert "█" * 5 in out
    assert "░" * 5 in out
