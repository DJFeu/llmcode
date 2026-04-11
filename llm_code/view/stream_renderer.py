"""ViewStreamRenderer — view-agnostic port of the TUI StreamingHandler.

M10.4 deliverable. Consumes the ``StreamEvent`` iterator produced by
``state.runtime.run_turn(...)`` and renders each event kind into the
current ``ViewBackend`` via its Protocol methods. Replaces the ~430
lines of widget-coupled logic in ``tui/streaming_handler.py`` with
~400 lines that work against any backend (REPL, future chat platforms,
test stub backends).

Key differences from the legacy handler:

- **State lives on AppState.** ``self._state.runtime``,
  ``self._state.cost_tracker``, ``self._state.input_tokens``, etc.
  replace the ``self._app._runtime`` / ``self._app._input_tokens``
  walks the old handler did into ``LLMCodeTUI``.
- **UI lives on ViewBackend.** Every ``chat.add_entry(widget)`` /
  ``query_one(...)`` call becomes a Protocol method call
  (``start_streaming_message``, ``start_tool_event``,
  ``update_status``, ``show_select``, ``print_info``, ...).
- **No Textual widgets imported.** The shared ``StreamParser`` from
  ``tui/stream_parser.py`` is pure logic and stays in place until M11
  relocates it; same for the two free-function diagnostic helpers
  (``_empty_response_message``, ``_truncation_warning_message``).
- **Thinking is surfaced as ``print_info``**, not as a bespoke
  ``ThinkingBlock`` widget. v2.0.0 has no dedicated collapsible
  widget yet, and the M10 invariant is "every feature works", not
  "every feature looks the same". Diagnostic content is preserved.
- **Active skill injection** is NOT handled here. The dispatcher
  (M10.6) will decide when to inject a skill's content into
  ``runtime.run_turn(active_skill_content=...)`` before calling
  ``renderer.run_turn(text)``. This keeps the renderer free of
  state-machine bookkeeping that belongs to the command layer.

Permission dialogs:
  v1.x ``StreamingHandler`` called ``self._app._dialogs.select(...)``
  which resolves to a Textual modal. v2.0.0 uses ``view.show_select``
  → M8 DialogPopover, with the same 5-choice menu (Allow / Always
  kind / Always exact / Edit / Deny). The ``Edit args`` branch opens
  ``view.show_text_input`` and round-trips the edited JSON through
  ``runtime.send_permission_response("edit", edited_args=parsed)``.
"""
from __future__ import annotations

import json as _json
import time
from typing import TYPE_CHECKING, Any, Optional

from llm_code.logging import get_logger
from llm_code.view.dialog_types import Choice, DialogCancelled
from llm_code.view.types import Role, StatusUpdate

if TYPE_CHECKING:
    from llm_code.runtime.app_state import AppState
    from llm_code.view.base import ViewBackend

logger = get_logger(__name__)


class ViewStreamRenderer:
    """Consume ``runtime.run_turn`` StreamEvents and render into a ViewBackend.

    Construction is cheap — the renderer holds only references to the
    view and the application state container. All per-turn state is
    local to ``run_turn``.

    Thread-safety: the renderer is single-turn-only. Dispatchers MUST
    await ``run_turn`` to completion (or exception) before calling it
    again. Two concurrent ``run_turn`` invocations would race on the
    shared AppState counters and runtime.
    """

    def __init__(self, view: "ViewBackend", state: "AppState") -> None:
        self._view = view
        self._state = state

    async def run_turn(
        self,
        user_input: str,
        images: Optional[list] = None,
        active_skill_content: Optional[str] = None,
    ) -> None:
        """Run a conversation turn.

        Yields nothing — all output flows through the view backend
        methods. ``images`` is forwarded to ``runtime.run_turn`` for
        multimodal providers. ``active_skill_content``, when provided
        by the dispatcher, is one-shot-injected into the runtime's
        system prompt for this turn only.
        """
        # Import event types lazily — keeps module import cheap and
        # avoids circular-import risk with runtime/api.
        from llm_code.api.types import (
            StreamCompactionDone,
            StreamCompactionStart,
            StreamMessageStop,
            StreamPermissionRequest,
            StreamTextDelta,
            StreamThinkingDelta,
            StreamToolExecResult,
            StreamToolExecStart,
            StreamToolProgress,
        )

        runtime = self._state.runtime
        if runtime is None:
            self._view.print_error(
                "runtime not initialized. Check configuration."
            )
            return

        # Mark the view as streaming. A matching is_streaming=False is
        # sent in the finally block below so a mid-turn exception
        # doesn't leave the status line stuck.
        self._view.update_status(StatusUpdate(is_streaming=True))
        self._view.on_turn_start()

        # Per-turn counters (snapshotted for the turn summary); the
        # session-total counters on AppState get incremented on
        # StreamMessageStop.
        turn_input_tokens = 0
        turn_output_tokens = 0
        self._state.context_warned = False

        # Tool-id → live ToolEventHandle so the Result event updates
        # the same handle the Start event created.
        pending_tools: dict[str, Any] = {}

        # Streaming message handle — lazily created on the first
        # visible text delta so we don't open an empty region if the
        # turn is tool-only.
        stream_handle: Any = None
        assistant_added = False
        thinking_buffer = ""
        thinking_start = time.monotonic()
        saw_tool_call_this_turn = False

        # Skill router: if enabled, run it before the LLM call so the
        # user sees which auto-skills matched.
        if runtime._skill_router is not None:
            try:
                matched = await runtime._skill_router.route_async(user_input)
                if matched:
                    self._view.print_info(
                        "[skills: " + ", ".join(s.name for s in matched) + "]"
                    )
            except Exception:
                logger.warning("skill router failed", exc_info=True)

        # Shared StreamParser: TEXT / THINKING / TOOL_CALL state machine
        # used by both runtime dispatch and view rendering. Imported
        # lazily so module import stays cheap.
        from llm_code.view.stream_parser import StreamEventKind, StreamParser

        _profile = getattr(runtime, "_model_profile", None)
        _implicit_thinking = (
            _profile.implicit_thinking if _profile is not None else False
        )
        _tool_names = frozenset()
        if self._state.tool_reg is not None:
            _tool_names = frozenset(
                t.name for t in self._state.tool_reg.all_tools()
            )
        _stream_parser = StreamParser(
            implicit_thinking=_implicit_thinking,
            known_tool_names=_tool_names,
        )

        def _flush_thinking() -> None:
            """Emit any accumulated thinking text as a [thinking] info
            block, then clear the buffer. Idempotent — a second call
            with an empty buffer is a no-op."""
            nonlocal thinking_buffer, thinking_start
            if not thinking_buffer.strip():
                thinking_buffer = ""
                return
            elapsed_t = time.monotonic() - thinking_start
            tokens_t = len(thinking_buffer) // 4
            self._view.print_info(
                f"[thinking: {tokens_t} tokens in {elapsed_t:.1f}s]\n"
                f"{thinking_buffer}"
            )
            thinking_buffer = ""

        def _ensure_stream_handle():
            """Create the streaming message handle on first visible
            text. Returns the handle so callers don't have to check
            ``stream_handle is None`` every time."""
            nonlocal stream_handle, assistant_added
            if stream_handle is None:
                stream_handle = self._view.start_streaming_message(
                    Role.ASSISTANT,
                )
                assistant_added = True
            return stream_handle

        # Sync plan mode flag from state to runtime before each turn.
        runtime.plan_mode = self._state.plan_mode

        start = time.monotonic()
        try:
            async for event in runtime.run_turn(
                user_input,
                images=images,
                active_skill_content=active_skill_content,
            ):
                if isinstance(event, StreamTextDelta):
                    for parsed_ev in _stream_parser.feed(event.text):
                        if parsed_ev.kind == StreamEventKind.THINKING:
                            if not thinking_buffer:
                                thinking_start = time.monotonic()
                            thinking_buffer += parsed_ev.text
                        elif parsed_ev.kind == StreamEventKind.TEXT:
                            _flush_thinking()
                            if parsed_ev.text:
                                _ensure_stream_handle().feed(parsed_ev.text)
                        elif parsed_ev.kind == StreamEventKind.TOOL_CALL:
                            _flush_thinking()
                            saw_tool_call_this_turn = True

                elif isinstance(event, StreamThinkingDelta):
                    if not thinking_buffer:
                        thinking_start = time.monotonic()
                    thinking_buffer += event.text

                elif isinstance(event, StreamToolExecStart):
                    # Flush any pending thinking so tool events land
                    # in the right narrative order.
                    _flush_thinking()
                    tool_handle = self._view.start_tool_event(
                        event.tool_name,
                        {"args_summary": event.args_summary},
                    )
                    if event.tool_id:
                        pending_tools[event.tool_id] = tool_handle

                elif isinstance(event, StreamToolExecResult):
                    existing = (
                        pending_tools.pop(event.tool_id, None)
                        if event.tool_id
                        else None
                    )
                    if existing is None:
                        # Fallback: no matching start (defensive). The
                        # runtime normally emits Start+Result pairs, but
                        # if a Result arrives without a tracked Start we
                        # still need to surface the output somehow.
                        existing = self._view.start_tool_event(
                            event.tool_name, {},
                        )
                    output = event.output[:200] if event.output else ""
                    if event.is_error:
                        existing.commit_failure(error=output)
                    else:
                        existing.commit_success(summary=output)

                elif isinstance(event, StreamToolProgress):
                    # The view may or may not display streaming tool
                    # progress; forward via update_status so backends
                    # that do can show a spinner/indicator.
                    self._view.update_status(
                        StatusUpdate(streaming_token_count=None)
                    )

                elif isinstance(event, StreamPermissionRequest):
                    # Flush any pending thinking so the permission
                    # dialog appears after the reasoning that led to it.
                    _flush_thinking()
                    await self._handle_permission_request(event)

                elif isinstance(event, StreamCompactionStart):
                    self._view.print_info(
                        f"[auto-compacting: {event.used_tokens}/"
                        f"{event.max_tokens} tokens]"
                    )

                elif isinstance(event, StreamCompactionDone):
                    self._view.print_info(
                        f"[compacted: {event.before_messages} → "
                        f"{event.after_messages} messages]"
                    )
                    self._view.on_session_compaction(
                        event.before_messages - event.after_messages,
                    )

                elif isinstance(event, StreamMessageStop):
                    self._state.last_stop_reason = (
                        event.stop_reason or "unknown"
                    )
                    if event.usage:
                        turn_input_tokens += event.usage.input_tokens
                        turn_output_tokens += event.usage.output_tokens
                        self._state.input_tokens += event.usage.input_tokens
                        self._state.output_tokens += event.usage.output_tokens
                        if self._state.cost_tracker is not None:
                            self._state.cost_tracker.add_usage(
                                event.usage.input_tokens,
                                event.usage.output_tokens,
                                cache_read_tokens=getattr(
                                    event.usage, "cache_read_tokens", 0,
                                ),
                                cache_creation_tokens=getattr(
                                    event.usage, "cache_creation_tokens", 0,
                                ),
                            )
                        self._push_status_update(event.usage.input_tokens)

        except Exception as exc:  # noqa: BLE001 — surface any runtime failure
            logger.warning("turn failed", exc_info=True)
            self._view.print_error(f"error: {exc}")
        finally:
            # Ensure the streaming region closes even on exception so
            # the scrollback doesn't end up with a dangling live region.
            if stream_handle is not None and stream_handle.is_active:
                try:
                    stream_handle.commit()
                except Exception:
                    logger.warning("stream handle commit failed", exc_info=True)
            self._view.update_status(StatusUpdate(is_streaming=False))
            self._view.on_turn_end()

        # Flush any residual buffered content from the parser
        for parsed_ev in _stream_parser.flush():
            if parsed_ev.kind == StreamEventKind.THINKING:
                thinking_buffer += parsed_ev.text
            elif parsed_ev.kind == StreamEventKind.TEXT and parsed_ev.text:
                _ensure_stream_handle().feed(parsed_ev.text)
                if stream_handle is not None and stream_handle.is_active:
                    stream_handle.commit()
            elif parsed_ev.kind == StreamEventKind.TOOL_CALL:
                saw_tool_call_this_turn = True

        # If nothing visible was emitted but we have thinking content,
        # surface the thinking as the answer. Reasoning models
        # (Qwen3, DeepSeek-R1) sometimes emit the entire useful
        # response inside <think> and never produce a final text
        # token, especially on short prompts.
        if not assistant_added and thinking_buffer.strip():
            handle = self._view.start_streaming_message(Role.ASSISTANT)
            handle.feed(thinking_buffer.strip())
            handle.commit()
            assistant_added = True
            thinking_buffer = ""
        elif not assistant_added and turn_output_tokens > 0:
            # Empty-response fallback — something was generated but
            # nothing visible landed. Run the diagnostic message
            # helper so the user understands *why*.
            thinking_len = len(thinking_buffer)
            logger.warning(
                "empty response fallback: out_tokens=%d thinking_len=%d "
                "saw_tool_call=%s assistant_added=%s stop_reason=%s "
                "thinking_head=%r",
                turn_output_tokens,
                thinking_len,
                saw_tool_call_this_turn,
                assistant_added,
                self._state.last_stop_reason,
                thinking_buffer[:120],
            )
            try:
                from llm_code.view.diagnostics import _empty_response_message
                session_msgs = (
                    runtime.session.messages if runtime.session else None
                )
                msg = _empty_response_message(
                    saw_tool_call=saw_tool_call_this_turn,
                    user_input=user_input,
                    session_messages=session_msgs,
                    turn_output_tokens=turn_output_tokens,
                    thinking_buffer_len=thinking_len,
                )
                self._view.print_warning(msg)
            except Exception:
                logger.warning(
                    "empty-response helper failed", exc_info=True,
                )

        # Any leftover thinking content that didn't become the primary
        # answer: flush it as a [thinking] info block so nothing is lost.
        if thinking_buffer.strip():
            elapsed_t = time.monotonic() - thinking_start
            tokens_t = len(thinking_buffer) // 4
            self._view.print_info(
                f"[thinking: {tokens_t} tokens in {elapsed_t:.1f}s]\n"
                f"{thinking_buffer}"
            )
            thinking_buffer = ""

        # Turn-complete diagnostic log (every turn, so 'truncated reply'
        # debug reports have a single log line with the full state).
        logger.debug(
            "turn complete: out_tokens=%d thinking_len=%d "
            "assistant_added=%s saw_tool_call=%s stop_reason=%s",
            turn_output_tokens,
            0,  # thinking_buffer was cleared above
            assistant_added,
            saw_tool_call_this_turn,
            self._state.last_stop_reason,
        )

        # Truncation warning: provider reported "length" / "max_tokens"
        # AND visible content was shown (so the empty-response path
        # didn't fire). The runtime's auto-upgrade handles most cases
        # but a hard provider cap can still leak through.
        if assistant_added and self._state.last_stop_reason in (
            "length", "max_tokens",
        ):
            try:
                from llm_code.view.diagnostics import _truncation_warning_message
                session_msgs = (
                    runtime.session.messages if runtime.session else None
                )
                warn_text = _truncation_warning_message(
                    stop_reason=self._state.last_stop_reason,
                    turn_output_tokens=turn_output_tokens,
                    user_input=user_input,
                    session_messages=session_msgs,
                )
                self._view.print_warning(warn_text)
                logger.warning(
                    "truncation warning shown: out_tokens=%d stop_reason=%s",
                    turn_output_tokens,
                    self._state.last_stop_reason,
                )
            except Exception:
                logger.warning(
                    "truncation helper failed", exc_info=True,
                )

        # Turn summary: a compact info line with elapsed + token + cost.
        elapsed = time.monotonic() - start
        cost = ""
        if self._state.cost_tracker is not None:
            try:
                cost = self._state.cost_tracker.format_cost()
            except Exception:
                cost = ""
        self._view.print_info(
            f"turn: {elapsed:.1f}s | in={turn_input_tokens} "
            f"out={turn_output_tokens}"
            + (f" | {cost}" if cost else "")
        )

    # === Permission dialog ===

    async def _handle_permission_request(self, event: Any) -> None:
        """Show the 5-choice permission dialog and forward the answer
        to the runtime.

        Extracted as a method so the main event loop stays readable.
        On DialogCancelled (user hit Esc), treat as 'deny' — same
        semantics as v1.x.
        """
        prompt_parts = [f"Tool: {event.tool_name}"]
        if event.args_preview:
            prompt_parts.append(event.args_preview)
        if event.diff_lines:
            prompt_parts.extend(event.diff_lines[:10])
        if event.pending_files:
            prompt_parts.append("Files: " + ", ".join(event.pending_files[:5]))
        prompt = "\n".join(prompt_parts)

        choices = [
            Choice(
                value="allow",
                label="Allow (y)",
                hint="Allow this tool call",
            ),
            Choice(
                value="always_kind",
                label="Always allow this type (a)",
                hint="Auto-allow this tool kind",
            ),
            Choice(
                value="always_exact",
                label="Always allow exact (A)",
                hint="Auto-allow this exact tool+args",
            ),
            Choice(
                value="edit",
                label="Edit args (e)",
                hint="Edit tool arguments before running",
            ),
            Choice(
                value="deny",
                label="Deny (n)",
                hint="Reject this tool call",
            ),
        ]

        runtime = self._state.runtime
        try:
            result = await self._view.show_select(
                prompt, choices, default="allow",
            )
        except DialogCancelled:
            runtime.send_permission_response("deny")
            return
        except Exception:
            logger.warning("permission dialog failed", exc_info=True)
            runtime.send_permission_response("deny")
            return

        if result == "edit":
            try:
                edited = await self._view.show_text_input(
                    f"Edit args for {event.tool_name}:",
                    default=event.args_preview or "{}",
                )
            except DialogCancelled:
                runtime.send_permission_response("deny")
                return
            try:
                parsed = _json.loads(edited)
                runtime.send_permission_response(
                    "edit", edited_args=parsed,
                )
            except _json.JSONDecodeError:
                self._view.print_warning(
                    "Invalid JSON — running with original args."
                )
                runtime.send_permission_response("allow")
        else:
            runtime.send_permission_response(result)

    # === Status push ===

    def _push_status_update(self, last_input_tokens: int) -> None:
        """Emit a StatusUpdate reflecting the latest token / cost
        state. Called once per StreamMessageStop event.

        ``last_input_tokens`` is the most recent turn's input token
        count, which the status line uses as the current context-fill
        approximation (input is the full re-sent conversation).
        """
        cost_usd = None
        if self._state.cost_tracker is not None:
            try:
                cost_usd = self._state.cost_tracker.total_cost_usd
            except Exception:
                cost_usd = None

        self._view.update_status(
            StatusUpdate(
                cost_usd=cost_usd,
                context_used_tokens=last_input_tokens,
                streaming_token_count=self._state.output_tokens,
            )
        )


__all__ = ["ViewStreamRenderer"]
