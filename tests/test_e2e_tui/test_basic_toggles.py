"""E2E: trivial state toggles — /clear, /yolo, /thinking, /vim, /cancel, /copy."""
from __future__ import annotations

from unittest.mock import MagicMock

from tests.test_e2e_tui.test_boot_banner import _rendered_text


# ── /clear ─────────────────────────────────────────────────────────────


async def test_clear_removes_all_chat_children(pilot_app):
    """`/clear` should wipe every entry in the chat scroll view, even
    the welcome banner."""
    from llm_code.tui.chat_view import ChatScrollView

    app, pilot = pilot_app
    chat = app.query_one(ChatScrollView)
    # Boot already queued at least one banner entry.
    assert len(list(chat.children)) > 0

    app._cmd_dispatcher.dispatch("clear", "")
    await pilot.pause()

    assert len(list(chat.children)) == 0


# ── /yolo ──────────────────────────────────────────────────────────────


async def test_yolo_requires_runtime(pilot_app):
    """`/yolo` without a runtime should print "Runtime not initialized"."""
    from llm_code.tui.chat_view import ChatScrollView

    app, pilot = pilot_app
    app._runtime = None
    chat = app.query_one(ChatScrollView)

    app._cmd_dispatcher.dispatch("yolo", "")
    await pilot.pause()

    rendered = _rendered_text(chat)
    assert "Runtime not initialized" in rendered


async def test_yolo_toggles_permission_mode_on_runtime(pilot_app):
    """`/yolo` with a runtime should flip the permission policy into
    YOLO mode (auto-accept)."""
    from llm_code.runtime.permissions import PermissionMode

    app, pilot = pilot_app

    policy = MagicMock()
    policy._mode = PermissionMode.WORKSPACE_WRITE
    runtime = MagicMock()
    runtime._permissions = policy
    app._runtime = runtime

    app._cmd_dispatcher.dispatch("yolo", "")
    await pilot.pause()

    # The policy must have been flipped — exact target mode depends
    # on the implementation, but it can't still be WORKSPACE_WRITE.
    assert policy._mode != PermissionMode.WORKSPACE_WRITE


# ── /thinking ──────────────────────────────────────────────────────────


async def test_thinking_bare_shows_current_mode(pilot_app):
    """`/thinking` alone should print the current mode + usage."""
    from llm_code.tui.chat_view import ChatScrollView

    app, pilot = pilot_app
    chat = app.query_one(ChatScrollView)

    app._cmd_dispatcher.dispatch("thinking", "")
    await pilot.pause()

    rendered = _rendered_text(chat)
    assert "Thinking:" in rendered
    assert "Usage: /thinking" in rendered


async def test_thinking_switch_to_on(pilot_app):
    """`/thinking on` should set the mode to `enabled` and echo it."""
    from llm_code.tui.chat_view import ChatScrollView

    app, pilot = pilot_app
    chat = app.query_one(ChatScrollView)

    app._cmd_dispatcher.dispatch("thinking", "on")
    await pilot.pause()

    assert app._config.thinking.mode == "enabled"
    rendered = _rendered_text(chat)
    assert "Thinking mode: enabled" in rendered


async def test_thinking_switch_to_adaptive(pilot_app):
    app, pilot = pilot_app
    app._cmd_dispatcher.dispatch("thinking", "adaptive")
    await pilot.pause()
    assert app._config.thinking.mode == "adaptive"


async def test_thinking_switch_to_off(pilot_app):
    app, pilot = pilot_app
    app._cmd_dispatcher.dispatch("thinking", "off")
    await pilot.pause()
    assert app._config.thinking.mode == "disabled"


# ── /vim ───────────────────────────────────────────────────────────────


async def test_vim_toggles_input_and_status_bar(pilot_app):
    """`/vim` flips the vim_mode reactive on both InputBar and StatusBar."""
    from llm_code.tui.input_bar import InputBar
    from llm_code.tui.status_bar import StatusBar

    app, pilot = pilot_app
    bar = app.query_one(InputBar)
    status = app.query_one(StatusBar)
    assert bar.vim_mode == ""
    assert status.vim_mode == ""

    app._cmd_dispatcher.dispatch("vim", "")
    await pilot.pause()
    assert bar.vim_mode == "NORMAL"
    assert status.vim_mode == "NORMAL"

    # Second call toggles back off.
    app._cmd_dispatcher.dispatch("vim", "")
    await pilot.pause()
    assert bar.vim_mode == ""
    assert status.vim_mode == ""


# ── /cancel ────────────────────────────────────────────────────────────


async def test_cancel_with_no_runtime_still_prints_message(pilot_app):
    """Cancel should print `(cancelled)` even when there's no active
    runtime — it's a best-effort signal, not a blocking op."""
    from llm_code.tui.chat_view import ChatScrollView

    app, pilot = pilot_app
    app._runtime = None
    chat = app.query_one(ChatScrollView)

    app._cmd_dispatcher.dispatch("cancel", "")
    await pilot.pause()

    rendered = _rendered_text(chat)
    assert "(cancelled)" in rendered


async def test_cancel_invokes_runtime_cancel_when_available(pilot_app):
    app, pilot = pilot_app
    runtime = MagicMock()
    runtime._cancel = MagicMock()
    app._runtime = runtime

    app._cmd_dispatcher.dispatch("cancel", "")
    await pilot.pause()

    runtime._cancel.assert_called_once()


# ── /copy ──────────────────────────────────────────────────────────────


async def test_copy_without_assistant_text_reports_empty(pilot_app):
    """`/copy` on a chat with no AssistantText should print "No
    response to copy"."""
    from llm_code.tui.chat_view import AssistantText, ChatScrollView

    app, pilot = pilot_app
    chat = app.query_one(ChatScrollView)
    # Wipe the boot banner. remove_children is async in Textual — wait
    # for it to fully tear down before dispatching the command, or the
    # handler will still see old entries.
    await chat.remove_children()
    await pilot.pause()
    # Double-check: no AssistantText entries left before we run /copy.
    remaining = [c for c in chat.children if isinstance(c, AssistantText)]
    assert remaining == []

    app._cmd_dispatcher.dispatch("copy", "")
    await pilot.pause()

    rendered = _rendered_text(chat)
    assert "No response to copy" in rendered


async def test_copy_copies_last_assistant_text_to_clipboard(pilot_app, monkeypatch):
    """`/copy` should call `copy_to_clipboard` with the most recent
    AssistantText's text."""
    from llm_code.tui.chat_view import AssistantText, ChatScrollView

    app, pilot = pilot_app
    chat = app.query_one(ChatScrollView)
    chat.remove_children()

    # Stash a known assistant response.
    target = AssistantText("the response we want to copy")
    chat.add_entry(target)
    # Add a later entry — the handler walks backwards so this should
    # not be copied.
    chat.add_entry(AssistantText("something else after"))

    captured = {}

    def _fake_copy(self, text):
        captured["text"] = text

    monkeypatch.setattr(type(app), "copy_to_clipboard", _fake_copy)

    app._cmd_dispatcher.dispatch("copy", "")
    await pilot.pause()

    # The last AssistantText in the children list is "something else
    # after" — but the handler walks children in reverse and copies
    # the first match, so that's what lands in the clipboard.
    assert "text" in captured
    assert captured["text"] == "something else after"
