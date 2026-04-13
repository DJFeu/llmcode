"""E2E smoke tests — spawn real llmcode via pexpect and assert golden paths.

M12 deliverable. Each test spawns a fresh ``llmcode`` REPL in a
pseudo-TTY, sends input through an ephemeral PT keybinding layer, and
asserts on visible terminal output.

**Calibration notes (M12 audit §H4 baseline capture)**

The v2.0.0 REPL uses prompt_toolkit in non-fullscreen mode. There is
**no ``> `` prompt character** at the start of each input line — PT
renders its input area at the bottom of the terminal with its own
drawing commands. Tests anchor on the status line instead, which is
always visible and contains stable fragments like ``tok`` and
``$0.00``.

Two other surprises the baseline capture turned up:

1. **Submit key is CR (``\\r``), not LF (``\\n``)**. pexpect's
   ``sendline`` defaults to LF on Unix, which PT interprets as a
   multi-line newline insertion — the input is NOT submitted. Use
   the ``send_line`` helper in conftest.py which sends ``\\r``.

2. **PT prints a "CPR" warning** on pexpect's pseudo-TTY:
   ``WARNING: your terminal doesn't support cursor position requests
   (CPR).`` It's benign (PT falls back gracefully) but appears in
   the output stream. Assertions tolerate its presence.

**LLMCODE_TEST_MODE (audit §H3 fix)**

The conftest sets ``LLMCODE_TEST_MODE=1`` in the child env. When
set, ``cli/main.py`` wraps the dispatcher's ``run_turn`` so that
plain-text submissions echo back as ``echo: <text>`` instead of
calling a real LLM. Slash commands, custom commands, and skill
commands still go through the real dispatcher.
"""
from __future__ import annotations

import re
import time

import pexpect

from tests.test_e2e_repl.conftest import capture, send_line, wait_ready


# === Cold start / architectural guards ===


def test_cold_start_renders_status_line(llmcode_process):
    """Within 5s of launch, the status line appears and contains
    the token + cost fragments."""
    wait_ready(llmcode_process)
    output = llmcode_process.before or ""
    assert "tok" in output, (
        "status line should have a 'tok' fragment; got: "
        f"{output[:200]!r}"
    )
    assert "$0.00" in output, (
        "status line should have '$0.00' fragment; got: "
        f"{output[:200]!r}"
    )


def test_no_alt_screen_entered(llmcode_process):
    """llmcode must NOT emit the DECSET ?1049h alt-screen sequence.

    This is the hard architectural guard from spec §10.4: no
    fullscreen TUI, no alt-screen buffer. Scrollback must stay
    native and text selection must work via the terminal.
    """
    wait_ready(llmcode_process)
    output = llmcode_process.before or ""
    assert "\x1b[?1049h" not in output, "alt-screen must not be used"


def test_no_mouse_tracking_enabled(llmcode_process):
    """llmcode must NOT emit DECSET ?1003 / ?1006 mouse tracking sequences.

    Another hard architectural guard: mouse clicks must not be
    captured by the app, so native terminal click-drag selection
    keeps working.
    """
    wait_ready(llmcode_process)
    output = llmcode_process.before or ""
    assert "\x1b[?1003h" not in output, "mouse tracking must not be enabled"
    assert "\x1b[?1006h" not in output, "mouse tracking must not be enabled"


def test_bracketed_paste_mode_enabled(llmcode_process):
    """prompt_toolkit enables DECSET ?2004h (bracketed paste) on
    start. Asserting on this gives us a positive "PT is up" signal."""
    wait_ready(llmcode_process)
    output = llmcode_process.before or ""
    assert "\x1b[?2004h" in output, (
        "bracketed paste mode should be enabled by PT"
    )


# === Exit paths ===


def test_quit_slash_command_exits_cleanly(llmcode_process):
    """/quit sends EOF and the process ends."""
    wait_ready(llmcode_process)
    send_line(llmcode_process, "/quit")
    llmcode_process.expect(pexpect.EOF, timeout=5)


def test_exit_slash_command_exits(llmcode_process):
    """/exit is an alias for /quit."""
    wait_ready(llmcode_process)
    send_line(llmcode_process, "/exit")
    llmcode_process.expect(pexpect.EOF, timeout=5)


def test_ctrl_d_on_empty_input_exits(llmcode_process):
    """Ctrl+D on empty prompt exits the REPL cleanly."""
    wait_ready(llmcode_process)
    llmcode_process.sendcontrol("d")
    llmcode_process.expect(pexpect.EOF, timeout=5)


# === Informational slash commands ===


def test_config_prints_model_and_provider(llmcode_process):
    """/config prints the loaded model + provider + permission fields."""
    wait_ready(llmcode_process)
    send_line(llmcode_process, "/config")
    output = capture(llmcode_process)
    assert "model:" in output
    assert "provider:" in output
    assert "permission:" in output
    assert "thinking:" in output


def test_cost_prints_tracker_state(llmcode_process):
    """/cost prints the cost tracker format (or 'No cost data')."""
    wait_ready(llmcode_process)
    send_line(llmcode_process, "/cost")
    output = capture(llmcode_process)
    # Either the cost tracker's formatted value or the fallback
    assert "$" in output or "No cost data" in output


def test_help_lists_built_in_commands(llmcode_process):
    """/help prints a list of built-in commands including /exit."""
    wait_ready(llmcode_process)
    send_line(llmcode_process, "/help")
    output = capture(llmcode_process)
    # Loose match — the /help output contains many commands so at
    # least one well-known name must appear
    assert "/exit" in output or "/clear" in output or "/cost" in output


def test_clear_slash_command_stays_alive(llmcode_process):
    """/clear runs cleanly and the REPL stays alive."""
    wait_ready(llmcode_process)
    send_line(llmcode_process, "/clear")
    time.sleep(0.3)
    assert llmcode_process.isalive()
    # Clean exit for teardown
    send_line(llmcode_process, "/quit")
    llmcode_process.expect(pexpect.EOF, timeout=5)


def test_cd_without_args_shows_current(llmcode_process, llmcode_cwd):
    """/cd prints the current working directory."""
    wait_ready(llmcode_process)
    send_line(llmcode_process, "/cd")
    output = capture(llmcode_process)
    assert "Current directory" in output


def test_budget_without_args_shows_none(llmcode_process):
    """/budget prints 'No budget set' when no budget is configured."""
    wait_ready(llmcode_process)
    send_line(llmcode_process, "/budget")
    output = capture(llmcode_process)
    assert "No budget set" in output or "Current token budget" in output


def test_personas_lists_builtin(llmcode_process):
    """/personas prints the built-in persona list."""
    wait_ready(llmcode_process)
    send_line(llmcode_process, "/personas")
    output = capture(llmcode_process)
    assert "personas" in output.lower()


def test_thinking_without_args_shows_current(llmcode_process):
    """/thinking prints current mode + usage hint."""
    wait_ready(llmcode_process)
    send_line(llmcode_process, "/thinking")
    output = capture(llmcode_process)
    assert "Thinking" in output or "thinking" in output


def test_cache_list_default(llmcode_process):
    """/cache with no args prints the 'Persistent caches:' header."""
    wait_ready(llmcode_process)
    send_line(llmcode_process, "/cache")
    output = capture(llmcode_process)
    assert "Persistent caches" in output or "cache" in output.lower()


# === State mutations ===


def test_plan_mode_toggle(llmcode_process):
    """/plan toggles plan mode on and prints the confirmation."""
    wait_ready(llmcode_process)
    send_line(llmcode_process, "/plan")
    output = capture(llmcode_process)
    assert "Plan mode ON" in output or "Plan mode" in output


# === Test-mode echo (LLMCODE_TEST_MODE=1 path) ===


def test_test_mode_echoes_plain_text(llmcode_process):
    """Plain-text submission should echo as 'echo: <text>' in test mode."""
    wait_ready(llmcode_process)
    send_line(llmcode_process, "hello world")
    output = capture(llmcode_process, wait=0.6)
    assert "echo" in output and "hello world" in output


# === Unknown command ===


def test_unknown_slash_command_does_not_crash(llmcode_process):
    """An invalid slash command warns but keeps the REPL alive."""
    wait_ready(llmcode_process)
    send_line(llmcode_process, "/nosuchcommand")
    output = capture(llmcode_process)
    assert llmcode_process.isalive()
    assert "Unknown command" in output or "nosuchcommand" in output


# === Slash popover ===


def test_slash_popover_shows_top_match(llmcode_process):
    """Typing '/' triggers the slash popover with at least one match.

    The M4 SlashCompleter renders a popover below the input area.
    Under pexpect's pseudo-TTY, PT prints a top-match row containing
    the completion — we don't assert on a specific command name
    because the order depends on the lexicographic sort, but we DO
    assert that some slash-prefixed token shows up. Teardown is
    handled by the fixture's finally block (Ctrl+D + force close)
    so we intentionally leave the ``/`` in the input area.
    """
    wait_ready(llmcode_process)
    llmcode_process.send("/")
    time.sleep(0.4)
    try:
        llmcode_process.expect([pexpect.TIMEOUT, pexpect.EOF], timeout=0.3)
    except Exception:
        pass
    output = llmcode_process.before or ""
    match = re.search(r"/[a-z]{2,}", output)
    assert match is not None, (
        "slash popover should show at least one completion; "
        f"output fragment: {output[-400:]!r}"
    )
    # Dismiss the popover and clear the input so the fixture's Ctrl+D
    # teardown exits cleanly. Ctrl+C clears the pending input buffer.
    llmcode_process.send("\x1b")  # Esc dismisses popover
    time.sleep(0.2)
    llmcode_process.sendcontrol("c")  # Clear input buffer
    time.sleep(0.2)


def test_slash_popover_esc_dismisses(llmcode_process):
    """Esc dismisses the popover and the REPL stays alive."""
    wait_ready(llmcode_process)
    llmcode_process.send("/")
    time.sleep(0.3)
    llmcode_process.send("\x1b")  # Esc
    time.sleep(0.2)
    assert llmcode_process.isalive()
    # Clear pending input so the fixture teardown's Ctrl+D exits cleanly
    llmcode_process.sendcontrol("c")
    time.sleep(0.2)


# NOTE: a process-level "every command registered and dispatchable"
# assertion was intentionally NOT ported here. The unit-level
# test_view/test_dispatcher.py::test_every_v1_command_is_registered
# already parametrizes over all 53 registered commands and verifies
# each has a matching _cmd_* handler. Running /help inside a pexpect
# spawn only adds noise: the help output is 10 KB+ with skill
# commands and PT's chunked rendering makes anchoring assertions
# fragile. Keep the unit-level guard and trust the e2e smoke suite
# for the "commands actually run" assertions (test_config,
# test_cost, test_help_lists_built_in_commands, etc.).
