# llm_code/tui/streaming_handler.py
"""StreamingHandler — extracted from app.py _run_turn (~430 lines).

Manages a single conversation turn: sets up UI state, iterates through
stream events (text delta, thinking, tool start/result, permission,
compaction), and shows turn summaries.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from llm_code.logging import get_logger
from llm_code.tui.chat_view import ChatScrollView, AssistantText, SkillBadge
from llm_code.tui.input_bar import InputBar
from llm_code.tui.status_bar import StatusBar

if TYPE_CHECKING:
    from llm_code.tui.app import LLMCodeTUI

logger = get_logger(__name__)


class StreamingHandler:
    """Handles a full streaming conversation turn for the TUI.

    Stores a back-reference to the owning ``LLMCodeTUI`` app so it can
    access runtime, config, widgets, and other shared state.
    """

    def __init__(self, app: "LLMCodeTUI") -> None:
        self._app = app

    async def run_turn(self, user_input: str, images: list | None = None) -> None:
        """Run a conversation turn with full streaming event handling.

        If _active_skill is set, its content is injected into the system prompt.
        """
        import asyncio
        import time
        from llm_code.api.types import (
            StreamPermissionRequest, StreamTextDelta, StreamThinkingDelta,
            StreamToolExecStart, StreamToolExecResult, StreamToolProgress,
            StreamMessageStop, StreamCompactionStart, StreamCompactionDone,
        )
        from llm_code.tui.chat_widgets import (
            SpinnerLine, ThinkingBlock, ToolBlock, TurnSummary,
        )

        if self._app._runtime is None:
            chat = self._app.query_one(ChatScrollView)
            chat.add_entry(AssistantText("Error: runtime not initialized. Check configuration."))
            return

        chat = self._app.query_one(ChatScrollView)
        input_bar = self._app.query_one(InputBar)
        status = self._app.query_one(StatusBar)

        input_bar.disabled = True
        status.is_streaming = True
        status.turn_count = int(getattr(status, "turn_count", 0) or 0) + 1

        # Reset per-turn counters
        turn_input_tokens = 0
        turn_output_tokens = 0
        self._app._context_warned = False
        # Per-turn map: tool_id → live ToolBlock (so Result events update
        # the same widget that was created at Start, no second mount)
        _pending_tools: dict[str, ToolBlock] = {}

        # Show skill router activations (run router here so user sees it
        # before the LLM call starts)
        _tui_cfg = getattr(self._app._config, "tui", None)
        _verb_override = tuple(getattr(_tui_cfg, "spinner_verbs", ()) or ())
        _verb_mode = getattr(_tui_cfg, "spinner_verbs_mode", "append") or "append"
        if self._app._runtime._skill_router is not None:
            _routing_spinner = SpinnerLine(
                verb_override=_verb_override, verb_mode=_verb_mode,
            )
            _routing_spinner.phase = "routing"
            chat.add_entry(_routing_spinner)
            try:
                _matched = await self._app._runtime._skill_router.route_async(user_input)
                if _matched:
                    chat.add_entry(SkillBadge([s.name for s in _matched]))
            except Exception:
                import logging
                logging.getLogger(__name__).warning(
                    "skill router failed", exc_info=True,
                )
            finally:
                try:
                    _routing_spinner.remove()
                except Exception:
                    pass

        spinner = SpinnerLine(
            verb_override=_verb_override, verb_mode=_verb_mode,
        )
        spinner.phase = "waiting"
        chat.add_entry(spinner)
        start = time.monotonic()

        async def update_spinner():
            while input_bar.disabled:
                await asyncio.sleep(0.1)
                spinner.elapsed = time.monotonic() - start
                spinner.advance_frame()

        timer_task = asyncio.create_task(update_spinner())

        assistant = AssistantText()
        assistant_added = False
        thinking_buffer = ""
        thinking_start = time.monotonic()
        # Canonical stream parser: single state machine shared with the
        # runtime dispatch path (llm_code.streaming.stream_parser). Emits
        # TEXT / THINKING / TOOL_CALL events that we route into the TUI
        # widgets below. Replaced ~110 lines of inline tag parsing.
        from llm_code.tui.stream_parser import StreamEventKind, StreamParser
        # Auto-detect implicit thinking: if the runtime's config has
        # thinking mode enabled, the vLLM chat template likely injects
        # <think>\n into the assistant prompt prefix so only the
        # closing tag appears in the stream. Starting the parser in
        # implicit_thinking mode ensures early content is classified
        # as THINKING, not TEXT — avoiding the retroactive
        # reclassification problem (#8 StreamParser implicit-think-end).
        # Read implicit_thinking from the model profile (authoritative)
        # instead of probing config.thinking.mode.
        _profile = getattr(self._app._runtime, "_model_profile", None)
        _implicit_thinking = _profile.implicit_thinking if _profile else False
        # Pass known tool names so the parser detects bare <tool_name>
        # tags (variant 5) and classifies them as TOOL_CALL, not TEXT.
        _tool_names = frozenset()
        if self._app._tool_reg:
            _tool_names = frozenset(t.name for t in self._app._tool_reg.all_tools())
        _stream_parser = StreamParser(
            implicit_thinking=_implicit_thinking,
            known_tool_names=_tool_names,
        )
        _saw_tool_call_this_turn = False  # For empty-response diagnosis

        async def remove_spinner() -> None:
            """Remove spinner if it is currently mounted."""
            if spinner.is_mounted:
                await spinner.remove()

        # Sync plan mode flag to runtime before each turn
        self._app._runtime.plan_mode = self._app._plan_mode

        try:
            # Consume active skill content (one-shot injection)
            _skill_content = None
            if hasattr(self._app, "_active_skill") and self._app._active_skill is not None:
                _skill_content = self._app._active_skill.content
                self._app._active_skill = None

            async for event in self._app._runtime.run_turn(
                user_input, images=images, active_skill_content=_skill_content,
            ):
                if isinstance(event, StreamTextDelta):
                    # Delegate all <think> / <tool_call> tag recognition
                    # to the shared StreamParser. It produces TEXT,
                    # THINKING, and TOOL_CALL events that we route into
                    # TUI widgets below.
                    for parsed_ev in _stream_parser.feed(event.text):
                        if parsed_ev.kind == StreamEventKind.THINKING:
                            if not thinking_buffer:
                                # First thinking content this turn — start
                                # the elapsed timer and set spinner phase.
                                thinking_start = time.monotonic()
                                spinner.phase = "thinking"
                            thinking_buffer += parsed_ev.text
                        elif parsed_ev.kind == StreamEventKind.TEXT:
                            # Flush any pending thinking into a ThinkingBlock
                            # before rendering visible text.
                            if thinking_buffer.strip():
                                elapsed_t = time.monotonic() - thinking_start
                                tokens_t = len(thinking_buffer) // 4
                                chat.add_entry(
                                    ThinkingBlock(thinking_buffer, elapsed_t, tokens_t)
                                )
                                thinking_buffer = ""
                            if parsed_ev.text:
                                if not assistant_added:
                                    await remove_spinner()
                                    chat.add_entry(assistant)
                                    assistant_added = True
                                assistant.append_text(parsed_ev.text)
                        elif parsed_ev.kind == StreamEventKind.TOOL_CALL:
                            # Flush any pending thinking before the tool
                            # call (so it's rendered in the right order).
                            if thinking_buffer.strip():
                                elapsed_t = time.monotonic() - thinking_start
                                tokens_t = len(thinking_buffer) // 4
                                chat.add_entry(
                                    ThinkingBlock(thinking_buffer, elapsed_t, tokens_t)
                                )
                                thinking_buffer = ""
                            # The runtime parser will re-detect and
                            # dispatch the call; TUI just records the
                            # fact for the empty-response diagnostic.
                            _saw_tool_call_this_turn = True
                    chat.resume_auto_scroll()

                elif isinstance(event, StreamThinkingDelta):
                    spinner.phase = "thinking"
                    thinking_buffer += event.text

                elif isinstance(event, StreamToolExecStart):
                    await remove_spinner()
                    tool_widget = ToolBlock.create(
                        event.tool_name, event.args_summary, "", is_error=False,
                    )
                    chat.add_entry(tool_widget)
                    # Track by tool_id so the matching Result event updates
                    # this widget in place instead of mounting a second one.
                    if event.tool_id:
                        _pending_tools[event.tool_id] = tool_widget
                    spinner.phase = "running"
                    spinner._tool_name = event.tool_name
                    chat.add_entry(spinner)

                elif isinstance(event, StreamToolExecResult):
                    await remove_spinner()
                    existing = _pending_tools.pop(event.tool_id, None) if event.tool_id else None
                    if existing is not None:
                        # Update the running widget in place — no second block
                        existing.update_result(
                            event.output[:200], event.is_error,
                        )
                    else:
                        # Fallback: no matching start (shouldn't happen with
                        # paired emit, but handle gracefully)
                        tool_widget = ToolBlock.create(
                            event.tool_name, "", event.output[:200], event.is_error,
                        )
                        chat.add_entry(tool_widget)
                    spinner.phase = "processing"
                    thinking_start = time.monotonic()
                    chat.add_entry(spinner)

                elif isinstance(event, StreamToolProgress):
                    spinner.phase = "running"
                    spinner._tool_name = event.tool_name

                elif isinstance(event, StreamPermissionRequest):
                    await remove_spinner()
                    # Show permission as a modal select dialog
                    _perm_prompt = f"Tool: {event.tool_name}"
                    if event.args_preview:
                        _perm_prompt += f"\n{event.args_preview}"
                    if event.diff_lines:
                        _perm_prompt += "\n" + "\n".join(event.diff_lines[:10])
                    if event.pending_files:
                        _perm_prompt += "\nFiles: " + ", ".join(event.pending_files[:5])
                    from llm_code.tui.dialogs import Choice
                    _perm_choices = [
                        Choice(value="allow", label="Allow (y)", hint="Allow this tool call"),
                        Choice(value="always_kind", label="Always allow this type (a)", hint="Auto-allow this tool kind"),
                        Choice(value="always_exact", label="Always allow exact (A)", hint="Auto-allow this exact tool+args"),
                        Choice(value="edit", label="Edit args (e)", hint="Edit tool arguments before running"),
                        Choice(value="deny", label="Deny (n)", hint="Reject this tool call"),
                    ]
                    try:
                        _perm_result = await self._app._dialogs.select(
                            _perm_prompt, _perm_choices, default="allow",
                        )
                        if _perm_result == "edit":
                            # Open a text editor with the current args as JSON
                            import json as _json
                            _edited = await self._app._dialogs.text(
                                f"Edit args for {event.tool_name}:",
                                default=event.args_preview or "{}",
                                multiline=True,
                            )
                            try:
                                _parsed = _json.loads(_edited)
                                self._app._runtime.send_permission_response("edit", edited_args=_parsed)
                            except _json.JSONDecodeError:
                                chat.add_entry(AssistantText("Invalid JSON — running with original args."))
                                self._app._runtime.send_permission_response("allow")
                        else:
                            self._app._runtime.send_permission_response(_perm_result)
                    except Exception:
                        self._app._runtime.send_permission_response("deny")

                elif isinstance(event, StreamCompactionStart):
                    spinner.phase = "compacting"
                    try:
                        chat.add_entry(AssistantText(
                            f"[auto-compacting: {event.used_tokens}/{event.max_tokens} tokens]"
                        ))
                    except Exception:
                        pass

                elif isinstance(event, StreamCompactionDone):
                    try:
                        chat.add_entry(AssistantText(
                            f"[compacted: {event.before_messages} → {event.after_messages} messages]"
                        ))
                    except Exception:
                        pass

                elif isinstance(event, StreamMessageStop):
                    # Capture the provider's stop_reason so the turn-
                    # end diagnostics can tell the user *why* the
                    # model stopped. Previously this field was read
                    # only by the runtime's auto-upgrade logic and
                    # never surfaced to the TUI, so a truncation
                    # that slipped past that path was invisible.
                    self._app._last_stop_reason = event.stop_reason or "unknown"
                    if event.usage:
                        turn_input_tokens += event.usage.input_tokens
                        turn_output_tokens += event.usage.output_tokens
                        self._app._input_tokens += event.usage.input_tokens
                        self._app._output_tokens += event.usage.output_tokens
                        if self._app._cost_tracker:
                            # Wave2-2: forward cache token buckets so cache
                            # reads (10% of input price) and cache writes
                            # (125%) are priced correctly instead of being
                            # counted as zero.
                            self._app._cost_tracker.add_usage(
                                event.usage.input_tokens,
                                event.usage.output_tokens,
                                cache_read_tokens=getattr(event.usage, "cache_read_tokens", 0),
                                cache_creation_tokens=getattr(event.usage, "cache_creation_tokens", 0),
                            )
                        # Real-time status bar update
                        status.tokens = self._app._output_tokens
                        if self._app._cost_tracker:
                            cost_usd = self._app._cost_tracker.total_cost_usd
                            status.cost = f"${cost_usd:.4f}" if cost_usd > 0.0001 else ""
                        # Context window meter: input tokens approximate
                        # current context fill (input is the full re-sent state).
                        status.context_used = event.usage.input_tokens
                        if (
                            not self._app._context_warned
                            and status.context_limit > 0
                            and status.context_pct() >= 80.0
                        ):
                            self._app._context_warned = True
                            chat.add_entry(AssistantText(
                                "⚠ Context window is "
                                f"{int(status.context_pct())}% full. "
                                "Run /compact to summarize older messages "
                                "and free space."
                            ))

        except Exception as exc:
            chat.add_entry(AssistantText(f"Error: {exc}"))
        finally:
            timer_task.cancel()
            try:
                await remove_spinner()
            except Exception:
                pass
            input_bar.disabled = False
            status.is_streaming = False

        # Flush any remaining buffered content from the shared parser
        for parsed_ev in _stream_parser.flush():
            if parsed_ev.kind == StreamEventKind.THINKING:
                thinking_buffer += parsed_ev.text
            elif parsed_ev.kind == StreamEventKind.TEXT and parsed_ev.text:
                if not assistant_added:
                    chat.add_entry(assistant)
                    assistant_added = True
                assistant.append_text(parsed_ev.text)
            elif parsed_ev.kind == StreamEventKind.TOOL_CALL:
                _saw_tool_call_this_turn = True

        if thinking_buffer.strip():
            elapsed_t = time.monotonic() - thinking_start
            tokens_t = len(thinking_buffer) // 4
            chat.add_entry(ThinkingBlock(thinking_buffer, elapsed_t, tokens_t))

        # If no text was ever displayed but we DO have thinking content,
        # surface the thinking as the answer. Reasoning models (Qwen3,
        # DeepSeek-R1) sometimes emit the entire useful response inside
        # the <think> block and never produce a "final" text token,
        # especially on short prompts where they reason themselves into
        # silence. Better to show the reasoning than a cryptic error.
        if not assistant_added and thinking_buffer.strip():
            chat.add_entry(AssistantText(thinking_buffer.strip()))
            assistant_added = True
        elif not assistant_added and turn_output_tokens > 0:
            # Distinguish why visible output is empty. The most common cause
            # (now that thinking parsing is robust) is that the model emitted
            # only a <tool_call> XML block — which gets stripped from the
            # visible stream — for a query that doesn't actually need a tool.
            # Pick message language to match the user's input language.
            #
            # Diagnostic log: everything the user might need to debug
            # the empty-response cause is captured in a single warning
            # line so `-v` runs have the full state immediately.
            _thinking_len = len(thinking_buffer)
            logger.warning(
                "empty response fallback: out_tokens=%d thinking_len=%d "
                "saw_tool_call=%s assistant_added=%s stop_reason=%s "
                "thinking_head=%r",
                turn_output_tokens,
                _thinking_len,
                _saw_tool_call_this_turn,
                assistant_added,
                getattr(self._app, "_last_stop_reason", "unknown"),
                thinking_buffer[:120],
            )
            from llm_code.tui.app import _empty_response_message
            chat.add_entry(AssistantText(
                _empty_response_message(
                    saw_tool_call=_saw_tool_call_this_turn,
                    user_input=user_input,
                    session_messages=getattr(self._app._runtime, "session", None) and self._app._runtime.session.messages,
                    turn_output_tokens=turn_output_tokens,
                    thinking_buffer_len=_thinking_len,
                )
            ))

        # Unconditional turn-end diagnostic so every turn (not just
        # the empty-response path) has a single log line capturing
        # the full state. Useful for "my reply seems truncated"
        # reports where the TUI shows SOME text but the user suspects
        # content went missing.
        _stop_reason = getattr(self._app, "_last_stop_reason", "unknown")
        logger.debug(
            "turn complete: out_tokens=%d thinking_len=%d "
            "assistant_added=%s saw_tool_call=%s stop_reason=%s",
            turn_output_tokens,
            len(thinking_buffer),
            assistant_added,
            _saw_tool_call_this_turn,
            _stop_reason,
        )

        # Truncation warning: if the provider reported finish_reason
        # == "length" / "max_tokens" AND some visible content was
        # shown (so the empty-response fallback didn't fire), the
        # user's reply was cut off mid-generation. The runtime's
        # auto-upgrade path handles most cases but a provider that
        # caps hard can still leak through. Show a subtle but
        # explicit warning so the user knows what happened instead
        # of puzzling over a truncated list.
        if assistant_added and _stop_reason in ("length", "max_tokens"):
            from llm_code.tui.app import _truncation_warning_message
            warn_text = _truncation_warning_message(
                stop_reason=_stop_reason,
                turn_output_tokens=turn_output_tokens,
                user_input=user_input,
                session_messages=getattr(self._app._runtime, "session", None) and self._app._runtime.session.messages,
            )
            chat.add_entry(AssistantText(warn_text))
            logger.warning(
                "truncation warning shown: out_tokens=%d stop_reason=%s",
                turn_output_tokens, _stop_reason,
            )

        elapsed = time.monotonic() - start
        cost = self._app._cost_tracker.format_cost() if self._app._cost_tracker else ""
        chat.add_entry(TurnSummary.create(elapsed, turn_input_tokens, turn_output_tokens, cost))

        status.tokens = self._app._output_tokens  # session total in status bar
        status.cost = cost
        chat.resume_auto_scroll()
