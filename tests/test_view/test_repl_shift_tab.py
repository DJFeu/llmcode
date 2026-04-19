"""Shift+Tab plan/build toggle binding.

Completes the loop promised by ``PLAN_MODE_DENY_MESSAGE`` — ('switch
to build mode (Shift+Tab) to execute mutating tools'). The key binding
now exists, fires a user-supplied callback, and the callback hooks
through ``PermissionPolicy.switch_to`` so the build-switch reminder
auto-injects on the next turn just like ``/mode build`` does.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from prompt_toolkit.buffer import Buffer

from llm_code.view.repl.history import PromptHistory
from llm_code.view.repl.keybindings import build_keybindings


def _build(on_plan_toggle=None):
    return build_keybindings(
        input_buffer=Buffer(),
        history=PromptHistory(),
        on_submit=lambda _: None,
        on_exit=lambda: None,
        on_plan_toggle=on_plan_toggle,
    )


def _find_binding(kb, key_seq: tuple[str, ...]):
    """Locate a binding by the key enum ``.value`` strings.

    prompt_toolkit stores keys as ``Keys`` enum members whose ``.value``
    matches the string form accepted by ``kb.add`` (e.g. ``"s-tab"`` →
    ``Keys.BackTab`` whose ``.value`` is ``"s-tab"``).
    """
    for b in kb.bindings:
        values = tuple(getattr(k, "value", str(k)) for k in b.keys)
        if values == key_seq:
            return b
    return None


class TestShiftTabBinding:
    def test_binding_registered_when_callback_supplied(self) -> None:
        kb = _build(on_plan_toggle=lambda: None)
        binding = _find_binding(kb, ("s-tab",))
        assert binding is not None, "Shift+Tab binding missing when callback supplied"

    def test_binding_absent_when_callback_missing(self) -> None:
        """Without a callback the binding must not register — otherwise
        Shift+Tab would silently eat the keystroke with no effect."""
        kb = _build(on_plan_toggle=None)
        assert _find_binding(kb, ("s-tab",)) is None

    def test_callback_fires_on_press(self) -> None:
        fired = []
        kb = _build(on_plan_toggle=lambda: fired.append(True))
        binding = _find_binding(kb, ("s-tab",))

        event = MagicMock()
        binding.handler(event)
        assert fired == [True]
