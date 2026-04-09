"""Wave2-6: contract tests for the Dialogs Protocol + two backends.

The test module is structured so the core contract runs against both
``ScriptedDialogs`` and ``HeadlessDialogs`` without duplication —
whichever impl is being exercised, the same spec should pass. This
is what makes swapping backends at runtime safe: the Protocol is
not just a type hint, it's a tested contract.

The Textual backend will land in a follow-up PR (it needs screen
push/pop integration with the running app); when it does, these
same tests should run against it too.
"""
from __future__ import annotations

import io

import pytest

from llm_code.tui.dialogs import (
    Choice,
    DialogCancelled,
    DialogValidationError,
    Dialogs,
    HeadlessDialogs,
    ScriptedDialogs,
)


# ---------- Protocol surface + import hygiene ----------

def test_scripted_dialogs_satisfies_protocol() -> None:
    """Duck-type check: does ScriptedDialogs expose the four async
    methods the Protocol requires?"""
    d = ScriptedDialogs()
    assert callable(d.confirm)
    assert callable(d.select)
    assert callable(d.text)
    assert callable(d.checklist)


def test_headless_dialogs_satisfies_protocol() -> None:
    d = HeadlessDialogs(
        input_stream=io.StringIO(),
        output_stream=io.StringIO(),
    )
    assert callable(d.confirm)
    assert callable(d.select)
    assert callable(d.text)
    assert callable(d.checklist)


def test_choice_is_frozen() -> None:
    c = Choice(value="a", label="First")
    with pytest.raises(Exception):
        c.value = "b"  # type: ignore[misc]


def test_choice_optional_fields_default() -> None:
    c = Choice(value=1, label="one")
    assert c.hint is None
    assert c.disabled is False


# ---------- ScriptedDialogs ----------

@pytest.mark.asyncio
async def test_scripted_confirm_returns_pushed_value() -> None:
    d = ScriptedDialogs()
    d.push_confirm(True)
    assert await d.confirm("run?") is True
    d.assert_drained()


@pytest.mark.asyncio
async def test_scripted_confirm_raises_when_queue_empty() -> None:
    d = ScriptedDialogs()
    with pytest.raises(AssertionError, match="no.*response was enqueued"):
        await d.confirm("run?")


@pytest.mark.asyncio
async def test_scripted_confirm_cancel() -> None:
    d = ScriptedDialogs()
    d.push_cancel("confirm")
    with pytest.raises(DialogCancelled):
        await d.confirm("run?")


@pytest.mark.asyncio
async def test_scripted_select_validates_value_is_a_choice() -> None:
    d = ScriptedDialogs()
    d.push_select("not-a-choice")
    with pytest.raises(AssertionError, match="not a valid choice"):
        await d.select(
            "pick a model",
            [Choice("claude-sonnet-4-6", "Sonnet"), Choice("claude-opus-4-6", "Opus")],
        )


@pytest.mark.asyncio
async def test_scripted_select_returns_valid_value() -> None:
    d = ScriptedDialogs()
    d.push_select("claude-opus-4-6")
    value = await d.select(
        "pick a model",
        [Choice("claude-sonnet-4-6", "Sonnet"), Choice("claude-opus-4-6", "Opus")],
    )
    assert value == "claude-opus-4-6"
    d.assert_drained()


@pytest.mark.asyncio
async def test_scripted_text_runs_validator() -> None:
    d = ScriptedDialogs()
    d.push_text("")
    with pytest.raises(DialogValidationError):
        await d.text(
            "commit message:",
            validator=lambda s: None if s else "must not be empty",
        )


@pytest.mark.asyncio
async def test_scripted_text_valid_value_passes_validator() -> None:
    d = ScriptedDialogs()
    d.push_text("refactor parser")
    result = await d.text(
        "commit message:",
        validator=lambda s: None if s else "must not be empty",
    )
    assert result == "refactor parser"


@pytest.mark.asyncio
async def test_scripted_checklist_validates_bounds() -> None:
    d = ScriptedDialogs()
    d.push_checklist(["a"])
    items = [Choice("a", "A"), Choice("b", "B"), Choice("c", "C")]
    with pytest.raises(AssertionError, match="min_select"):
        await d.checklist("pick at least 2", items, min_select=2)


@pytest.mark.asyncio
async def test_scripted_checklist_valid_selection() -> None:
    d = ScriptedDialogs()
    d.push_checklist(["a", "c"])
    items = [Choice("a", "A"), Choice("b", "B"), Choice("c", "C")]
    result = await d.checklist("pick", items)
    assert result == ["a", "c"]


def test_scripted_assert_drained_catches_unused_response() -> None:
    d = ScriptedDialogs()
    d.push_confirm(True)
    with pytest.raises(AssertionError, match="unconsumed"):
        d.assert_drained()


def test_scripted_calls_log_records_prompt() -> None:
    """The .calls attribute lets tests assert the exact prompt text
    the code under test showed the user — catches refactors that
    accidentally change the wording of a destructive confirm."""
    d = ScriptedDialogs()
    d.push_confirm(True)
    import asyncio
    asyncio.run(d.confirm("Delete /tmp/foo?"))
    assert d.calls == [("confirm", "Delete /tmp/foo?")]


# ---------- HeadlessDialogs ----------

def _make_headless(stdin: str, *, assume_yes: bool = False) -> tuple[HeadlessDialogs, io.StringIO]:
    in_buf = io.StringIO(stdin)
    out_buf = io.StringIO()
    return (
        HeadlessDialogs(
            input_stream=in_buf, output_stream=out_buf, assume_yes=assume_yes,
        ),
        out_buf,
    )


@pytest.mark.asyncio
async def test_headless_confirm_y_returns_true() -> None:
    d, _ = _make_headless("y\n")
    assert await d.confirm("run?") is True


@pytest.mark.asyncio
async def test_headless_confirm_n_returns_false() -> None:
    d, _ = _make_headless("n\n")
    assert await d.confirm("run?", default=True) is False


@pytest.mark.asyncio
async def test_headless_confirm_blank_uses_default() -> None:
    d, _ = _make_headless("\n")
    assert await d.confirm("run?", default=True) is True

    d2, _ = _make_headless("\n")
    assert await d2.confirm("run?", default=False) is False


@pytest.mark.asyncio
async def test_headless_confirm_eof_raises_cancelled() -> None:
    d, _ = _make_headless("")  # empty stdin → immediate EOF
    with pytest.raises(DialogCancelled):
        await d.confirm("run?")


@pytest.mark.asyncio
async def test_headless_confirm_danger_marker_in_output() -> None:
    d, out = _make_headless("y\n")
    await d.confirm("DELETE database?", danger=True)
    assert "⚠" in out.getvalue()


@pytest.mark.asyncio
async def test_headless_assume_yes_returns_default_without_prompting() -> None:
    """--yes mode: default is returned, no prompt written."""
    d, out = _make_headless("", assume_yes=True)
    assert await d.confirm("run?", default=True) is True
    assert out.getvalue() == ""  # no prompt written


@pytest.mark.asyncio
async def test_headless_select_picks_index() -> None:
    d, _ = _make_headless("2\n")
    value = await d.select(
        "pick a model",
        [
            Choice("sonnet", "Sonnet"),
            Choice("opus", "Opus"),
        ],
    )
    assert value == "opus"


@pytest.mark.asyncio
async def test_headless_select_blank_uses_default() -> None:
    d, _ = _make_headless("\n")
    value = await d.select(
        "pick",
        [Choice("a", "A"), Choice("b", "B")],
        default="b",
    )
    assert value == "b"


@pytest.mark.asyncio
async def test_headless_select_out_of_range_raises_cancelled() -> None:
    d, _ = _make_headless("99\n")
    with pytest.raises(DialogCancelled):
        await d.select("pick", [Choice("a", "A")])


@pytest.mark.asyncio
async def test_headless_select_disabled_choice_raises() -> None:
    d, _ = _make_headless("1\n")
    with pytest.raises(DialogCancelled):
        await d.select(
            "pick",
            [Choice("a", "A", disabled=True), Choice("b", "B")],
        )


@pytest.mark.asyncio
async def test_headless_text_single_line() -> None:
    d, _ = _make_headless("commit subject\n")
    assert await d.text("msg:") == "commit subject"


@pytest.mark.asyncio
async def test_headless_text_blank_returns_default() -> None:
    d, _ = _make_headless("\n")
    assert await d.text("msg:", default="WIP") == "WIP"


@pytest.mark.asyncio
async def test_headless_text_multiline_blank_line_terminates() -> None:
    d, _ = _make_headless("line1\nline2\n\n")
    assert await d.text("msg:", multiline=True) == "line1\nline2"


@pytest.mark.asyncio
async def test_headless_text_validator_rejects_input() -> None:
    d, _ = _make_headless("abc\n")
    with pytest.raises(DialogValidationError):
        await d.text(
            "numeric:",
            validator=lambda s: None if s.isdigit() else "not a number",
        )


@pytest.mark.asyncio
async def test_headless_checklist_comma_separated() -> None:
    d, _ = _make_headless("1,3\n")
    result = await d.checklist(
        "pick",
        [Choice("a", "A"), Choice("b", "B"), Choice("c", "C")],
    )
    assert result == ["a", "c"]


@pytest.mark.asyncio
async def test_headless_checklist_blank_means_empty() -> None:
    d, _ = _make_headless("\n")
    result = await d.checklist(
        "pick",
        [Choice("a", "A"), Choice("b", "B")],
    )
    assert result == []


@pytest.mark.asyncio
async def test_headless_checklist_min_select_enforced() -> None:
    d, _ = _make_headless("\n")
    with pytest.raises(DialogCancelled):
        await d.checklist(
            "pick at least 1",
            [Choice("a", "A"), Choice("b", "B")],
            min_select=1,
        )


@pytest.mark.asyncio
async def test_headless_checklist_max_select_enforced() -> None:
    d, _ = _make_headless("1,2,3\n")
    with pytest.raises(DialogCancelled):
        await d.checklist(
            "pick up to 2",
            [Choice("a", "A"), Choice("b", "B"), Choice("c", "C")],
            max_select=2,
        )


# ---------- Cross-backend contract: same spec, two backends ----------

async def _drive_simple_confirm(dialogs: Dialogs, expected: bool) -> None:
    """A minimal shared contract: calling confirm() returns the
    staged value regardless of which backend is driving."""
    result = await dialogs.confirm("proceed?", default=False)
    assert result is expected


@pytest.mark.asyncio
async def test_contract_confirm_scripted() -> None:
    d = ScriptedDialogs()
    d.push_confirm(True)
    await _drive_simple_confirm(d, True)


@pytest.mark.asyncio
async def test_contract_confirm_headless_y() -> None:
    d, _ = _make_headless("y\n")
    await _drive_simple_confirm(d, True)


@pytest.mark.asyncio
async def test_contract_confirm_headless_assume_yes() -> None:
    """``assume_yes=True`` returns the default — which this test
    sets to False — so both backends answering the "same" question
    can legitimately disagree on the outcome when the input shape
    is different. The contract is that each backend honors its own
    input channel, not that the final bool matches across backends."""
    d, _ = _make_headless("", assume_yes=True)
    await _drive_simple_confirm(d, False)
