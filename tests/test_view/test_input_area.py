"""InputArea + keybindings integration tests.

Transliterated from tests/test_tui/test_input_bar.py and
test_prompt_history_e2e.py, adjusted for the M4 architecture where
InputArea owns the Buffer and keybindings live in a factory function.
"""
from __future__ import annotations

import asyncio

import pytest

from llm_code.view.repl.history import PromptHistory
from llm_code.view.repl.keybindings import build_keybindings


# === Enter / submit ===


@pytest.mark.asyncio
async def test_enter_submits_buffer_to_handler(repl_pilot):
    """Enter on non-empty text schedules the input callback."""
    received: list[str] = []

    async def handler(text: str) -> None:
        received.append(text)

    repl_pilot.backend.set_input_handler(handler)

    input_area = repl_pilot.backend.coordinator._input_area
    input_area.buffer.insert_text("hello")
    await repl_pilot.press("enter")
    # Submit schedules an asyncio.Task — yield to the event loop briefly.
    await asyncio.sleep(0.01)

    assert received == ["hello"]
    assert input_area.buffer.text == ""  # buffer cleared after submit


@pytest.mark.asyncio
async def test_enter_on_empty_buffer_is_noop(repl_pilot):
    """Enter with an empty buffer does not fire the handler."""
    received: list[str] = []

    async def handler(text: str) -> None:
        received.append(text)

    repl_pilot.backend.set_input_handler(handler)
    await repl_pilot.press("enter")
    await asyncio.sleep(0.01)
    assert received == []


@pytest.mark.asyncio
async def test_enter_strips_whitespace_check(repl_pilot):
    """Enter strips leading/trailing whitespace before passing to handler."""
    received: list[str] = []

    async def handler(text: str) -> None:
        received.append(text)

    repl_pilot.backend.set_input_handler(handler)
    input_area = repl_pilot.backend.coordinator._input_area
    input_area.buffer.insert_text("  padded  ")
    await repl_pilot.press("enter")
    await asyncio.sleep(0.01)

    assert received == ["padded"]


@pytest.mark.asyncio
async def test_enter_records_in_history(repl_pilot, tmp_path):
    """Submitted entries are recorded in prompt history."""
    coord = repl_pilot.backend.coordinator
    coord._history = PromptHistory(path=tmp_path / "history.txt")

    input_area = coord._input_area
    input_area.buffer.insert_text("remember me")
    await repl_pilot.press("enter")
    await asyncio.sleep(0.01)

    assert "remember me" in coord._history.entries


# === Newline insertion (Ctrl+J / Alt+Enter) ===


@pytest.mark.asyncio
async def test_ctrl_j_inserts_newline(repl_pilot):
    """Ctrl+J inserts a literal newline into the buffer."""
    input_area = repl_pilot.backend.coordinator._input_area
    input_area.buffer.insert_text("line1")
    await repl_pilot.press("c-j")
    input_area.buffer.insert_text("line2")
    assert input_area.buffer.text == "line1\nline2"


# === Clear line (Ctrl+U) ===


@pytest.mark.asyncio
async def test_ctrl_u_clears_line(repl_pilot):
    """Ctrl+U empties the input buffer."""
    input_area = repl_pilot.backend.coordinator._input_area
    input_area.buffer.insert_text("partial draft")
    await repl_pilot.press("c-u")
    assert input_area.buffer.text == ""


# === History recall (Ctrl+Up / Ctrl+Down) ===


def _rebuild_keybindings_with_history(coord) -> None:
    """Re-run build_keybindings() so the new closure captures the
    freshly-assigned coord._history. The pilot's press() helper reads
    from coord._key_bindings, so replacing that attribute is enough —
    no need to rebuild the PT Application.
    """
    coord._key_bindings = build_keybindings(
        input_buffer=coord._input_area.buffer,
        history=coord._history,
        on_submit=coord._handle_submit,
        on_exit=coord.request_exit,
    )


@pytest.mark.asyncio
async def test_ctrl_up_recalls_previous_history(repl_pilot, tmp_path):
    """Ctrl+Up walks backward through history."""
    coord = repl_pilot.backend.coordinator
    coord._history = PromptHistory(path=tmp_path / "history.txt")
    coord._history.add("earlier")
    coord._history.add("latest")
    _rebuild_keybindings_with_history(coord)

    await repl_pilot.press("c-up")
    assert coord._input_area.buffer.text == "latest"
    await repl_pilot.press("c-up")
    assert coord._input_area.buffer.text == "earlier"


@pytest.mark.asyncio
async def test_ctrl_down_walks_forward_and_restores_draft(repl_pilot, tmp_path):
    """Ctrl+Down walks forward, restoring the composing draft on overflow."""
    coord = repl_pilot.backend.coordinator
    coord._history = PromptHistory(path=tmp_path / "history.txt")
    coord._history.add("older")
    coord._history.add("newer")
    _rebuild_keybindings_with_history(coord)

    # Start composing a draft
    coord._input_area.buffer.text = "my draft"
    # Walk back into history, then forward again
    await repl_pilot.press("c-up")
    assert coord._input_area.buffer.text == "newer"
    await repl_pilot.press("c-down")
    # Walking forward past newest restores the draft
    assert coord._input_area.buffer.text == "my draft"


@pytest.mark.asyncio
async def test_bare_up_does_not_recall_history(repl_pilot, tmp_path):
    """Regression guard: bare Up must never touch history.

    Bare 'up' has no binding in our factory — prompt_toolkit's default
    cursor-movement handler takes it. Either way, history state must
    stay quiescent.
    """
    coord = repl_pilot.backend.coordinator
    coord._history = PromptHistory(path=tmp_path / "h.txt")
    coord._history.add("should-not-recall")

    assert coord._input_area.buffer.text == ""
    assert not coord._history.is_navigating()

    # We can't press bare 'up' via our helper because no binding is
    # registered for it — which is exactly the property we want to pin.
    with pytest.raises(AssertionError, match="no binding"):
        await repl_pilot.press("up")

    assert not coord._history.is_navigating()


# === Ctrl+D exit ===


@pytest.mark.asyncio
async def test_ctrl_d_on_empty_input_requests_exit(repl_pilot):
    """Ctrl+D with an empty buffer sets the coordinator exit flag.

    We assert on the real side-effect (coord._exit_requested) rather
    than monkey-patching request_exit, because the keybinding captured
    the original method via closure at construction time.
    """
    coord = repl_pilot.backend.coordinator
    assert coord._exit_requested is False
    await repl_pilot.press("c-d")
    assert coord._exit_requested is True


@pytest.mark.asyncio
async def test_ctrl_d_on_non_empty_input_does_not_exit(repl_pilot):
    """Ctrl+D with buffer content leaves the exit flag alone."""
    coord = repl_pilot.backend.coordinator
    coord._input_area.buffer.insert_text("typed")
    await repl_pilot.press("c-d")
    assert coord._exit_requested is False
    # And the buffer is untouched by Ctrl+D-as-delete-char (M4 doesn't
    # wire that; M8+ may).
    assert coord._input_area.buffer.text == "typed"


# === Ctrl+C clear / exit ===


@pytest.mark.asyncio
async def test_ctrl_c_clears_non_empty_buffer(repl_pilot):
    """Ctrl+C on non-empty text clears the buffer without exiting."""
    coord = repl_pilot.backend.coordinator
    coord._input_area.buffer.insert_text("abort this")
    await repl_pilot.press("c-c")
    assert coord._input_area.buffer.text == ""
    assert coord._exit_requested is False


@pytest.mark.asyncio
async def test_ctrl_c_on_empty_buffer_requests_exit(repl_pilot):
    """Ctrl+C on empty buffer requests exit (shell-like second-press)."""
    coord = repl_pilot.backend.coordinator
    assert coord._input_area.buffer.text == ""
    await repl_pilot.press("c-c")
    assert coord._exit_requested is True


# === Voice hotkey wiring ===


@pytest.mark.asyncio
async def test_voice_hotkey_absent_when_not_wired(repl_pilot):
    """By default the coordinator does not register a Ctrl+G binding."""
    with pytest.raises(AssertionError, match="no binding"):
        await repl_pilot.press("c-g")


@pytest.mark.asyncio
async def test_voice_hotkey_fires_when_wired():
    """When on_voice_toggle is supplied, Ctrl+G fires it."""
    from prompt_toolkit.buffer import Buffer

    buf = Buffer(multiline=True)
    fired: list[bool] = []

    kb = build_keybindings(
        input_buffer=buf,
        history=PromptHistory(),
        on_submit=lambda t: None,
        on_exit=lambda: None,
        on_voice_toggle=lambda: fired.append(True),
    )
    # Locate the c-g binding
    from prompt_toolkit.key_binding.key_bindings import _parse_key
    matches = kb.get_bindings_for_keys((_parse_key("c-g"),))
    assert matches, "c-g should be registered when on_voice_toggle is set"

    class _FakeApp:
        def invalidate(self) -> None:
            pass

        def exit(self) -> None:
            pass

    class _FakeEvent:
        app = _FakeApp()

    matches[-1].handler(_FakeEvent())
    assert fired == [True]
