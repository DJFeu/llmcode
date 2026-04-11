"""SlashCompleter unit tests."""
from __future__ import annotations

from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document

from llm_code.view.repl.components.slash_popover import SlashCompleter


def _completions(completer: SlashCompleter, text: str):
    doc = Document(text=text, cursor_position=len(text))
    return list(completer.get_completions(doc, CompleteEvent()))


def test_no_completions_for_non_slash_text():
    """Regular typing produces no completions (popover stays hidden)."""
    c = SlashCompleter()
    assert _completions(c, "hello") == []


def test_no_completions_for_empty_text():
    c = SlashCompleter()
    assert _completions(c, "") == []


def test_completions_for_slash_prefix():
    """Bare '/' lists all registered commands."""
    c = SlashCompleter()
    results = _completions(c, "/")
    assert len(results) > 0
    assert all(comp.text.startswith("/") for comp in results)


def test_completions_filtered_by_prefix():
    """/voi filters to commands starting with /voi (includes /voice)."""
    c = SlashCompleter()
    results = _completions(c, "/voi")
    names = [comp.text for comp in results]
    assert "/voice" in names


def test_completions_narrow_as_prefix_grows():
    """/v returns more matches than /vo (monotonic narrowing)."""
    c = SlashCompleter()
    r1 = _completions(c, "/v")
    r2 = _completions(c, "/vo")
    assert len(r1) >= len(r2)


def test_completions_include_description():
    """Each completion carries a display_meta description string."""
    c = SlashCompleter()
    results = _completions(c, "/")
    with_desc = [r for r in results if r.display_meta]
    assert len(with_desc) > 0


def test_completion_start_position_is_negative_prefix_length():
    """start_position is -len(command_prefix) so PT replaces the token."""
    c = SlashCompleter()
    results = _completions(c, "/help")
    match = next((r for r in results if r.text == "/help"), None)
    assert match is not None
    # The prefix '/help' is 5 chars; start_position should be -5
    assert match.start_position == -5


def test_refresh_rescans_registry():
    """refresh() re-reads COMMAND_REGISTRY without losing entry count."""
    c = SlashCompleter()
    original_count = len(c._entries)
    c.refresh()
    assert len(c._entries) == original_count


def test_entries_are_sorted():
    """_entries is alphabetically sorted by command name."""
    c = SlashCompleter()
    names = [name for (name, _) in c._entries]
    assert names == sorted(names)


def test_unknown_prefix_yields_nothing():
    """/zzz-does-not-exist returns no matches."""
    c = SlashCompleter()
    results = _completions(c, "/zzz-nonexistent")
    assert results == []
