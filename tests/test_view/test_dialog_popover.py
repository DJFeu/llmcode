"""Tests for DialogPopover — four dialog types + key binding scoping."""
from __future__ import annotations

import asyncio

import pytest
from prompt_toolkit.filters import Condition

from llm_code.view.dialog_types import Choice, DialogCancelled
from llm_code.view.repl.components.dialog_popover import (
    ConfirmRequest,
    DialogPopover,
    build_dialog_float,
    build_dialog_key_bindings,
)
from llm_code.view.types import RiskLevel


@pytest.fixture
def popover() -> DialogPopover:
    return DialogPopover()


# === Confirm ===


@pytest.mark.asyncio
async def test_confirm_accept_y(popover):
    task = asyncio.create_task(popover.show_confirm("ok?"))
    await asyncio.sleep(0)
    popover.accept_positive()
    result = await task
    assert result is True
    assert popover.is_active() is False


@pytest.mark.asyncio
async def test_confirm_accept_n(popover):
    task = asyncio.create_task(popover.show_confirm("ok?"))
    await asyncio.sleep(0)
    popover.accept_negative()
    assert await task is False


@pytest.mark.asyncio
async def test_confirm_default_applies_on_submit(popover):
    task = asyncio.create_task(popover.show_confirm("ok?", default=True))
    await asyncio.sleep(0)
    popover.submit()  # no explicit Y/N, uses default
    assert await task is True


@pytest.mark.asyncio
async def test_confirm_default_false_on_submit(popover):
    task = asyncio.create_task(popover.show_confirm("ok?", default=False))
    await asyncio.sleep(0)
    popover.submit()
    assert await task is False


@pytest.mark.asyncio
async def test_confirm_cancel_raises(popover):
    task = asyncio.create_task(popover.show_confirm("ok?"))
    await asyncio.sleep(0)
    popover.cancel()
    with pytest.raises(DialogCancelled):
        await task


@pytest.mark.asyncio
async def test_confirm_active_is_confirm_request(popover):
    task = asyncio.create_task(popover.show_confirm("ok?", risk=RiskLevel.HIGH))
    await asyncio.sleep(0)
    assert isinstance(popover.active, ConfirmRequest)
    assert popover.active.risk == RiskLevel.HIGH
    popover.cancel()
    with pytest.raises(DialogCancelled):
        await task


@pytest.mark.asyncio
async def test_confirm_risk_level_in_style(popover):
    task = asyncio.create_task(
        popover.show_confirm("dangerous?", risk=RiskLevel.CRITICAL)
    )
    await asyncio.sleep(0)
    rendered = popover.render_formatted()
    assert any("critical" in seg[0].lower() for seg in rendered)
    popover.accept_negative()
    await task


@pytest.mark.asyncio
async def test_confirm_render_shows_default_marker(popover):
    task = asyncio.create_task(popover.show_confirm("ok?", default=True))
    await asyncio.sleep(0)
    text = "".join(seg[1] for seg in popover.render_formatted())
    assert "Y/n" in text
    popover.accept_negative()
    await task


# === Select ===


@pytest.mark.asyncio
async def test_select_default_cursor_and_submit(popover):
    task = asyncio.create_task(popover.show_select(
        "pick",
        choices=[Choice("a", "A"), Choice("b", "B"), Choice("c", "C")],
        default="b",
    ))
    await asyncio.sleep(0)
    assert popover.active.cursor == 1
    popover.submit()
    assert await task == "b"


@pytest.mark.asyncio
async def test_select_no_default_starts_at_zero(popover):
    task = asyncio.create_task(popover.show_select(
        "pick",
        choices=[Choice("a", "A"), Choice("b", "B")],
    ))
    await asyncio.sleep(0)
    assert popover.active.cursor == 0
    popover.submit()
    assert await task == "a"


@pytest.mark.asyncio
async def test_select_move_cursor_down(popover):
    task = asyncio.create_task(popover.show_select(
        "pick",
        choices=[Choice("a", "A"), Choice("b", "B")],
    ))
    await asyncio.sleep(0)
    popover.move_cursor(1)
    popover.submit()
    assert await task == "b"


@pytest.mark.asyncio
async def test_select_cursor_wraps_backward(popover):
    task = asyncio.create_task(popover.show_select(
        "pick",
        choices=[Choice("a", "A"), Choice("b", "B")],
    ))
    await asyncio.sleep(0)
    popover.move_cursor(-1)  # wrap to last
    popover.submit()
    assert await task == "b"


@pytest.mark.asyncio
async def test_select_cursor_wraps_forward(popover):
    task = asyncio.create_task(popover.show_select(
        "pick",
        choices=[Choice("a", "A"), Choice("b", "B")],
    ))
    await asyncio.sleep(0)
    popover.move_cursor(2)  # wraps around full cycle
    popover.submit()
    assert await task == "a"


@pytest.mark.asyncio
async def test_select_cancel(popover):
    task = asyncio.create_task(popover.show_select(
        "pick",
        choices=[Choice("a", "A"), Choice("b", "B")],
    ))
    await asyncio.sleep(0)
    popover.cancel()
    with pytest.raises(DialogCancelled):
        await task


@pytest.mark.asyncio
async def test_select_render_shows_cursor(popover):
    task = asyncio.create_task(popover.show_select(
        "pick",
        choices=[Choice("a", "A"), Choice("b", "B")],
        default="b",
    ))
    await asyncio.sleep(0)
    text = "".join(seg[1] for seg in popover.render_formatted())
    assert "pick" in text
    assert "▶" in text
    assert "A" in text
    assert "B" in text
    popover.cancel()
    with pytest.raises(DialogCancelled):
        await task


@pytest.mark.asyncio
async def test_select_choice_hint_in_render(popover):
    task = asyncio.create_task(popover.show_select(
        "pick",
        choices=[Choice("a", "A", hint="first letter")],
    ))
    await asyncio.sleep(0)
    text = "".join(seg[1] for seg in popover.render_formatted())
    assert "first letter" in text
    popover.cancel()
    with pytest.raises(DialogCancelled):
        await task


# === Text input ===


@pytest.mark.asyncio
async def test_text_input_types_and_submits(popover):
    task = asyncio.create_task(popover.show_text_input("name:"))
    await asyncio.sleep(0)
    popover.insert_text("alice")
    popover.submit()
    assert await task == "alice"


@pytest.mark.asyncio
async def test_text_input_default_populates_buffer(popover):
    task = asyncio.create_task(popover.show_text_input("x:", default="foo"))
    await asyncio.sleep(0)
    assert popover.active.buffer == "foo"
    popover.submit()
    assert await task == "foo"


@pytest.mark.asyncio
async def test_text_input_backspace(popover):
    task = asyncio.create_task(popover.show_text_input("x:"))
    await asyncio.sleep(0)
    popover.insert_text("hello")
    popover.delete_back()
    popover.submit()
    assert await task == "hell"


@pytest.mark.asyncio
async def test_text_input_backspace_on_empty(popover):
    """Backspace on empty buffer is a safe no-op."""
    task = asyncio.create_task(popover.show_text_input("x:"))
    await asyncio.sleep(0)
    popover.delete_back()  # no crash
    assert popover.active.buffer == ""
    popover.cancel()
    with pytest.raises(DialogCancelled):
        await task


@pytest.mark.asyncio
async def test_text_input_validator_rejects_then_accepts(popover):
    def validator(s):
        return None if "@" in s else "must contain @"

    task = asyncio.create_task(popover.show_text_input(
        "email:", validator=validator,
    ))
    await asyncio.sleep(0)
    popover.insert_text("notanemail")
    popover.submit()  # rejected
    assert popover.is_active()
    assert popover.active.error_message == "must contain @"

    popover.insert_text("@x.com")
    popover.submit()  # accepted
    assert await task == "notanemail@x.com"


@pytest.mark.asyncio
async def test_text_input_validator_error_clears_on_edit(popover):
    def validator(s):
        return None if s else "required"

    task = asyncio.create_task(popover.show_text_input(
        "name:", validator=validator,
    ))
    await asyncio.sleep(0)
    popover.submit()  # rejected (empty)
    assert popover.active.error_message == "required"
    popover.insert_text("a")
    # Editing clears the error message
    assert popover.active.error_message is None
    popover.submit()
    assert await task == "a"


@pytest.mark.asyncio
async def test_text_input_secret_masks_render(popover):
    task = asyncio.create_task(popover.show_text_input(
        "password:", secret=True,
    ))
    await asyncio.sleep(0)
    popover.insert_text("hunter2")
    rendered = popover.render_formatted()
    text = "".join(seg[1] for seg in rendered)
    assert "hunter2" not in text
    assert "*" in text
    popover.submit()
    assert await task == "hunter2"


@pytest.mark.asyncio
async def test_text_input_cancel(popover):
    task = asyncio.create_task(popover.show_text_input("x:"))
    await asyncio.sleep(0)
    popover.cancel()
    with pytest.raises(DialogCancelled):
        await task


# === Checklist ===


@pytest.mark.asyncio
async def test_checklist_toggle_and_submit(popover):
    task = asyncio.create_task(popover.show_checklist(
        "pick any",
        choices=[Choice("a", "A"), Choice("b", "B"), Choice("c", "C")],
    ))
    await asyncio.sleep(0)
    popover.toggle_current()  # select "a"
    popover.move_cursor(2)  # cursor on "c"
    popover.toggle_current()  # select "c"
    popover.submit()
    result = await task
    assert set(result) == {"a", "c"}


@pytest.mark.asyncio
async def test_checklist_toggle_twice_deselects(popover):
    task = asyncio.create_task(popover.show_checklist(
        "pick",
        choices=[Choice("a", "A"), Choice("b", "B")],
    ))
    await asyncio.sleep(0)
    popover.toggle_current()  # select "a"
    popover.toggle_current()  # deselect "a"
    popover.submit()
    assert list(await task) == []


@pytest.mark.asyncio
async def test_checklist_defaults_preselected(popover):
    task = asyncio.create_task(popover.show_checklist(
        "pick",
        choices=[Choice("a", "A"), Choice("b", "B")],
        defaults=["a"],
    ))
    await asyncio.sleep(0)
    assert "a" in popover.active.selected
    popover.submit()
    assert "a" in await task


@pytest.mark.asyncio
async def test_checklist_empty_submit_returns_empty(popover):
    task = asyncio.create_task(popover.show_checklist(
        "pick",
        choices=[Choice("a", "A"), Choice("b", "B")],
    ))
    await asyncio.sleep(0)
    popover.submit()
    assert list(await task) == []


@pytest.mark.asyncio
async def test_checklist_render_shows_check_markers(popover):
    task = asyncio.create_task(popover.show_checklist(
        "pick",
        choices=[Choice("a", "A"), Choice("b", "B")],
        defaults=["a"],
    ))
    await asyncio.sleep(0)
    text = "".join(seg[1] for seg in popover.render_formatted())
    assert "[x]" in text
    assert "[ ]" in text
    popover.submit()
    await task


@pytest.mark.asyncio
async def test_checklist_cancel(popover):
    task = asyncio.create_task(popover.show_checklist(
        "pick",
        choices=[Choice("a", "A")],
    ))
    await asyncio.sleep(0)
    popover.cancel()
    with pytest.raises(DialogCancelled):
        await task


# === Nested dialogs rejected ===


@pytest.mark.asyncio
async def test_nested_dialog_raises(popover):
    first_task = asyncio.create_task(popover.show_confirm("first"))
    await asyncio.sleep(0)
    with pytest.raises(RuntimeError, match="already active"):
        await popover.show_confirm("second")
    # Cleanly resolve the first task so pytest-asyncio doesn't warn.
    popover.accept_negative()
    assert await first_task is False


# === Idle state ===


def test_is_active_starts_false():
    p = DialogPopover()
    assert p.is_active() is False
    assert p.active is None


def test_render_empty_when_idle():
    p = DialogPopover()
    rendered = p.render_formatted()
    assert list(rendered) == []


def test_submit_idle_is_noop():
    p = DialogPopover()
    p.submit()  # should not crash
    assert p.is_active() is False


def test_cancel_idle_is_noop():
    p = DialogPopover()
    p.cancel()  # should not crash


def test_move_cursor_idle_is_noop():
    p = DialogPopover()
    p.move_cursor(1)  # should not crash


def test_toggle_current_idle_is_noop():
    p = DialogPopover()
    p.toggle_current()  # should not crash


# === build_dialog_float / build_dialog_key_bindings ===


def test_build_dialog_float_returns_float():
    from prompt_toolkit.layout.containers import Float
    p = DialogPopover()
    f = build_dialog_float(p)
    assert isinstance(f, Float)


def test_build_dialog_key_bindings_registers_keys():
    p = DialogPopover()
    kb = build_dialog_key_bindings(p)
    # Expect at least up/down/space/y/n/<any>/backspace/enter/escape (9)
    assert len(kb.bindings) >= 9


def _find_specific_binding(kb, key_name: str, handler_name_substr: str):
    """Locate the binding registered with a specific key (not via <any>).

    Needed because the wildcard <any> binding also appears in
    get_bindings_for_keys() for any printable key — the test has to
    disambiguate by handler name.
    """
    from prompt_toolkit.key_binding.key_bindings import _parse_key
    matches = kb.get_bindings_for_keys((_parse_key(key_name),))
    for b in matches:
        if handler_name_substr in b.handler.__name__:
            return b
    raise AssertionError(
        f"no binding for {key_name!r} matching handler {handler_name_substr!r}"
    )


@pytest.mark.asyncio
async def test_key_bindings_y_filter_only_in_confirm():
    """The 'y' binding's Filter should be True only for ConfirmRequest."""
    p = DialogPopover()
    kb = build_dialog_key_bindings(p)
    y_binding = _find_specific_binding(kb, "y", "_y")

    # No active dialog — filter should be False
    assert bool(y_binding.filter()) is False

    # Open a confirm dialog
    task = asyncio.create_task(p.show_confirm("ok?"))
    await asyncio.sleep(0)
    assert bool(y_binding.filter()) is True

    # Open a text input instead — y filter should flip to False
    p.cancel()
    with pytest.raises(DialogCancelled):
        await task
    task2 = asyncio.create_task(p.show_text_input("name:"))
    await asyncio.sleep(0)
    assert bool(y_binding.filter()) is False
    p.cancel()
    with pytest.raises(DialogCancelled):
        await task2


@pytest.mark.asyncio
async def test_key_bindings_any_filter_only_in_text_input():
    """The '<any>' binding's Filter should be True only for TextInputRequest."""
    from prompt_toolkit.key_binding.key_bindings import _parse_key

    p = DialogPopover()
    kb = build_dialog_key_bindings(p)
    any_bindings = kb.get_bindings_for_keys((_parse_key("a"),))
    # '<any>' matches every printable char
    assert len(any_bindings) >= 1
    # No dialog — inactive
    for b in any_bindings:
        assert bool(b.filter()) is False

    task = asyncio.create_task(p.show_text_input("name:"))
    await asyncio.sleep(0)
    # Now at least one of the matching bindings should be active (the <any> one)
    assert any(bool(b.filter()) for b in any_bindings)
    p.cancel()
    with pytest.raises(DialogCancelled):
        await task


# === Sanity: Filter keeps refs after reassignment ===


def test_filter_construction():
    """Guard: Condition(callable) is callable with no args and returns bool."""
    p = DialogPopover()
    c = Condition(p.is_active)
    assert bool(c()) is False
