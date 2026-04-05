"""Core agentic conversation runtime: turn loop with streaming and tool execution."""
from __future__ import annotations

import asyncio
import dataclasses
import json
import time
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any, AsyncIterator

from pydantic import ValidationError

from llm_code.logging import get_logger
from llm_code.api.types import (
    Message,
    MessageRequest,
    StreamEvent,
    StreamMessageStop,
    StreamPermissionRequest,
    StreamTextDelta,
    StreamToolExecResult,
    StreamToolExecStart,
    StreamToolProgress,
    StreamToolUseInputDelta,
    StreamToolUseStart,
    TextBlock,
    TokenUsage,
    ToolResultBlock,
    ToolUseBlock,
)
from llm_code.runtime.compressor import ContextCompressor
from llm_code.runtime.cost_tracker import BudgetExceededError
from llm_code.runtime.permissions import PermissionOutcome
from llm_code.runtime.streaming_executor import StreamingToolExecutor
from llm_code.runtime.telemetry import Telemetry, get_noop_telemetry
from llm_code.tools.base import PermissionLevel, ToolResult
from llm_code.tools.parsing import ParsedToolCall, parse_tool_calls

if TYPE_CHECKING:
    from llm_code.runtime.context import ProjectContext
    from llm_code.runtime.permissions import PermissionPolicy
    from llm_code.runtime.prompt import SystemPromptBuilder
    from llm_code.runtime.session import Session
    from llm_code.tools.registry import ToolRegistry


def build_thinking_extra_body(thinking_config, *, is_local: bool = False) -> dict | None:
    """Build extra_body dict for thinking mode configuration.

    Returns None for adaptive mode (let provider decide),
    explicit enable/disable dict for other modes.

    Local models get unlimited thinking budget (no cost concern).
    """
    mode = thinking_config.mode
    if mode == "enabled":
        # Local models: no budget cap; cloud: use configured budget
        budget = thinking_config.budget_tokens
        if is_local:
            budget = max(budget, 131072)  # At least 128K tokens for local
        return {
            "chat_template_kwargs": {
                "enable_thinking": True,
                "thinking_budget": budget,
            }
        }
    if mode == "disabled":
        return {"chat_template_kwargs": {"enable_thinking": False}}
    # adaptive: for local models, enable with generous budget; for cloud, let provider decide
    if is_local:
        budget = max(thinking_config.budget_tokens, 131072)
        return {
            "chat_template_kwargs": {
                "enable_thinking": True,
                "thinking_budget": budget,
            }
        }
    return None


# Thread pool for running blocking tool execution off the event loop
_TOOL_EXECUTOR = ThreadPoolExecutor(max_workers=4)

logger = get_logger(__name__)

# Maximum number of characters to inline in tool results
_MAX_INLINE_RESULT = 4000


@dataclasses.dataclass(frozen=True)
class TurnSummary:
    iterations: int
    total_usage: TokenUsage


# ---------------------------------------------------------------------------
# ConversationRuntime
# ---------------------------------------------------------------------------

class ConversationRuntime:
    """Agentic loop that drives LLM turns, tool execution, and session updates."""

    def __init__(
        self,
        provider: Any,
        tool_registry: "ToolRegistry",
        permission_policy: "PermissionPolicy",
        hook_runner: Any,
        prompt_builder: "SystemPromptBuilder",
        config: Any,
        session: "Session",
        context: "ProjectContext",
        checkpoint_manager: Any = None,
        token_budget: Any = None,
        vcr_recorder: Any = None,
        deferred_tool_manager: Any = None,
        telemetry: Telemetry | None = None,
        recovery_checkpoint: Any = None,
        cost_tracker: Any = None,
        skills: Any = None,
        mcp_manager: Any = None,
        memory_store: Any = None,
        task_manager: Any = None,
        project_index: Any = None,
    ) -> None:
        self._provider = provider
        self._tool_registry = tool_registry
        self._permissions = permission_policy
        self._hooks = hook_runner
        self._prompt_builder = prompt_builder
        self._config = config
        self.session = session
        self._context = context
        self._checkpoint_mgr = checkpoint_manager
        self._token_budget = token_budget
        self._vcr_recorder = vcr_recorder
        self._deferred_tool_manager = deferred_tool_manager
        self._telemetry: Telemetry = telemetry if telemetry is not None else get_noop_telemetry()
        self._recovery_checkpoint = recovery_checkpoint
        self._cost_tracker = cost_tracker
        self._skills = skills
        self._mcp_manager = mcp_manager
        self._memory_store = memory_store
        self._task_manager = task_manager
        self._project_index = project_index
        self._permission_future: asyncio.Future[str] | None = None
        self._has_attempted_reactive_compact = False
        self._consecutive_failures: int = 0
        self._compressor = ContextCompressor()
        self._active_model: str = getattr(config, "model", "")
        self._hida_classifier: Any | None = None
        self._hida_engine: Any | None = None
        self._last_hida_profile: Any | None = None

        # Initialize HIDA if enabled in config
        if getattr(config, "hida", None) is not None and config.hida.enabled:
            try:
                from llm_code.hida.classifier import TaskClassifier
                from llm_code.hida.engine import HidaEngine
                from llm_code.hida.profiles import DEFAULT_PROFILES
                self._hida_classifier = TaskClassifier(profiles=DEFAULT_PROFILES)
                self._hida_engine = HidaEngine()
            except ImportError:
                pass

    def _fire_hook(self, event: str, context: dict | None = None) -> None:
        """Fire a hook event if the hook runner supports the generic fire() method."""
        if hasattr(self._hooks, "fire"):
            self._hooks.fire(event, context or {})

    def send_permission_response(self, response: str) -> None:
        """Resolve the pending permission prompt with 'allow', 'deny', or 'always'.

        Called by the TUI when the user presses y/n/a on a permission inline widget.

        IMPORTANT: Must be called from the same event loop thread that owns
        ``_permission_future``. In Textual, this is guaranteed when called from
        an ``on_key`` handler since both the app and ``run_worker`` share the
        same asyncio event loop.
        """
        if self._permission_future is not None and not self._permission_future.done():
            self._permission_future.set_result(response)

    async def run_turn(self, user_input: str, images: list | None = None) -> AsyncIterator[StreamEvent]:
        """Run one user turn (may involve multiple LLM calls for tool use)."""
        logger.debug("Starting turn: %s", user_input[:80])
        _turn_start = time.monotonic()
        self._fire_hook("prompt_submit", {"text": user_input[:200]})
        if self._vcr_recorder is not None:
            self._vcr_recorder.record("user_input", {"text": user_input})
        # 1. Add user message to session (with optional images)
        content_blocks: list = [TextBlock(text=user_input)]
        if images:
            content_blocks.extend(images)
        user_msg = Message(role="user", content=tuple(content_blocks))
        self.session = self.session.add_message(user_msg)

        accumulated_usage = TokenUsage(input_tokens=0, output_tokens=0)
        self._has_attempted_reactive_compact = False
        force_xml = getattr(self, "_force_xml_mode", False)
        # Token limit auto-upgrade state: reset each turn, doubles on max_tokens stop
        _current_max_tokens: int = self._config.max_tokens
        # Local models (localhost/private network) have no cost concern — no cap
        _base_url = getattr(self._config, "provider_base_url", "") or ""
        _is_local = any(h in _base_url for h in ("localhost", "127.0.0.1", "0.0.0.0", "192.168.", "10.", "172."))
        _TOKEN_UPGRADE_CAP = 0 if _is_local else 65536  # 0 means unlimited

        for _iteration in range(self._config.max_turn_iterations):
            # Budget enforcement: check before each LLM call
            if self._cost_tracker is not None:
                try:
                    self._cost_tracker.check_budget()
                except BudgetExceededError as exc:
                    yield StreamTextDelta(
                        text=f"Budget limit (${exc.budget:.2f}) reached. Use /budget to increase."
                    )
                    return

            # HIDA dynamic context filtering
            allowed_tool_names: set[str] | None = None

            if (
                self._hida_classifier is not None
                and self._hida_engine is not None
                and getattr(self._config, "hida", None) is not None
                and self._config.hida.enabled
            ):
                hida_profile = await self._hida_classifier.classify(
                    user_input,
                    provider=self._provider if hasattr(self._provider, "complete") else None,
                    confidence_threshold=self._config.hida.confidence_threshold,
                )
                self._last_hida_profile = hida_profile

                if not hida_profile.load_full_prompt:
                    all_tool_names = {t.name for t in self._tool_registry.all_tools()}
                    allowed_tool_names = self._hida_engine.filter_tools(hida_profile, all_tool_names)

            # 2. Build system prompt
            use_native = getattr(self._provider, "supports_native_tools", lambda: True)() and not force_xml

            # Deferred tool loading: when a manager is present, split tools into
            # visible and deferred; inject a hint into the system prompt.
            _deferred_hint: str | None = None
            if self._deferred_tool_manager is not None:
                all_defs = list(self._tool_registry.definitions(allowed=allowed_tool_names))
                max_visible = getattr(self._config, "max_visible_tools", 20)
                visible_list, deferred_list = self._deferred_tool_manager.select_tools(
                    all_defs, max_visible=max_visible
                )
                tool_defs = tuple(visible_list)
                if deferred_list:
                    _deferred_count = len(deferred_list)
                    _deferred_hint = (
                        "## Tool Discovery\n\n"
                        f"There are {_deferred_count} additional tool(s) not shown here. "
                        "Use the 'tool_search' tool with a query to find and unlock them."
                    )
            else:
                tool_defs = self._tool_registry.definitions(
                    allowed=allowed_tool_names,
                )

            # Collect MCP instructions if manager is available
            _mcp_instructions: dict[str, str] | None = None
            if self._mcp_manager is not None:
                _mcp_instructions = self._mcp_manager.get_all_instructions() or None

            # Collect memory entries if store is available
            _memory_entries: dict | None = None
            if self._memory_store is not None and hasattr(self._memory_store, "list_entries"):
                try:
                    _memory_entries = self._memory_store.list_entries() or None
                except Exception:
                    pass

            system_prompt = self._prompt_builder.build(
                self._context,
                tools=tool_defs,
                native_tools=use_native,
                skills=self._skills,
                mcp_instructions=_mcp_instructions,
                memory_entries=_memory_entries,
                task_manager=self._task_manager,
                project_index=self._project_index,
            )
            if _deferred_hint:
                system_prompt = system_prompt + "\n\n" + _deferred_hint
            self._fire_hook("prompt_compile", {"prompt_length": len(system_prompt), "tool_count": len(tool_defs)})

            # 3. Create request and stream
            request = MessageRequest(
                model=self._active_model,
                messages=self.session.messages,
                system=system_prompt,
                tools=tool_defs if use_native else (),
                max_tokens=_current_max_tokens,
                temperature=self._config.temperature,
                extra_body=build_thinking_extra_body(self._config.thinking, is_local=_is_local) if not use_native else None,
            )

            if self._vcr_recorder is not None:
                self._vcr_recorder.record("llm_request", {
                    "model": request.model,
                    "max_tokens": request.max_tokens,
                })

            # Error recovery: tool choice fallback + reactive compact
            self._fire_hook("http_request", {"model": self._active_model, "url": getattr(self._config, "provider_base_url", "")})
            try:
                stream = await self._provider.stream_message(request)
            except Exception as exc:
                _exc_str = str(exc)
                self._fire_hook("http_error", {"error": _exc_str[:200], "model": self._active_model})
                # Auto-fallback: if native tool calling is not supported by server
                if "tool-call-parser" in _exc_str or "tool choice" in _exc_str.lower():
                    logger.warning("Server does not support native tool calling; falling back to XML tag mode")
                    self._fire_hook("http_fallback", {"reason": "xml_mode", "model": self._active_model})
                    self._force_xml_mode = True
                    # Rebuild request without tools
                    system_prompt = self._prompt_builder.build(
                        self._context,
                        tools=tool_defs,
                        native_tools=False,
                        skills=self._skills,
                        mcp_instructions=_mcp_instructions,
                        memory_entries=_memory_entries,
                        task_manager=self._task_manager,
                        project_index=self._project_index,
                    )
                    request = MessageRequest(
                        model=self._active_model,
                        messages=self.session.messages,
                        system=system_prompt,
                        tools=(),
                        max_tokens=_current_max_tokens,
                        temperature=self._config.temperature,
                        extra_body=build_thinking_extra_body(self._config.thinking, is_local=_is_local),
                    )
                    stream = await self._provider.stream_message(request)
                elif (
                    ("413" in _exc_str or "prompt too long" in _exc_str.lower())
                    and not self._has_attempted_reactive_compact
                ):
                    logger.warning("Prompt too long; compacting context and retrying")
                    self._fire_hook("session_compact", {"reason": "prompt_too_long"})
                    self._has_attempted_reactive_compact = True
                    _compressor = ContextCompressor()
                    self.session = _compressor.compress(
                        self.session,
                        self._config.compact_after_tokens // 2,
                    )
                    continue  # retry this iteration of the turn loop
                else:
                    # Layer 3: model fallback — track consecutive provider errors
                    self._consecutive_failures += 1
                    _fallback = getattr(
                        getattr(self._config, "model_routing", None), "fallback", ""
                    )
                    if _fallback and self._active_model != _fallback:
                        # Still have retries remaining before switching — retry same model
                        if self._consecutive_failures < 3:
                            self._fire_hook("http_retry", {"attempt": self._consecutive_failures, "model": self._active_model})
                            logger.warning(
                                "Provider error (attempt %d/3): %s",
                                self._consecutive_failures,
                                exc,
                            )
                            continue  # retry this iteration
                        # 3rd consecutive failure: switch to fallback model
                        self._fire_hook("http_fallback", {"reason": "consecutive_failures", "from": self._active_model, "to": _fallback})
                        logger.warning(
                            "3 consecutive provider errors; switching from %s to fallback model %s",
                            self._active_model,
                            _fallback,
                        )
                        self._active_model = _fallback
                        continue  # retry with fallback model
                    logger.error("Provider stream error: %s", exc)
                    raise

            # 4. Collect events and buffers
            text_parts: list[str] = []
            native_tool_calls: dict[str, dict] = {}  # id -> {id, name, json_parts}
            native_tool_list: list[dict] = []
            stop_event: StreamMessageStop | None = None

            # StreamingToolExecutor: starts read-only tools in background while streaming
            _streaming_executor = StreamingToolExecutor(self._tool_registry, self._permissions)
            _current_streaming_tool_id: str | None = None

            async for event in stream:
                # Yield streaming events to caller
                yield event

                if isinstance(event, StreamTextDelta):
                    text_parts.append(event.text)
                elif isinstance(event, StreamToolUseStart):
                    # Finalize the previously streaming tool (if any) before starting new one
                    if _current_streaming_tool_id is not None:
                        _streaming_executor.finalize(_current_streaming_tool_id)
                    _current_streaming_tool_id = event.id
                    native_tool_calls[event.id] = {
                        "id": event.id,
                        "name": event.name,
                        "json_parts": [],
                    }
                    _streaming_executor.start_tool(event.id, event.name)
                elif isinstance(event, StreamToolUseInputDelta):
                    if event.id in native_tool_calls:
                        native_tool_calls[event.id]["json_parts"].append(event.partial_json)
                    _streaming_executor.submit(event.id, event.partial_json)
                elif isinstance(event, StreamMessageStop):
                    # Finalize the last streaming tool
                    if _current_streaming_tool_id is not None:
                        _streaming_executor.finalize(_current_streaming_tool_id)
                        _current_streaming_tool_id = None
                    stop_event = event

            # Reset consecutive failure counter on successful stream
            self._consecutive_failures = 0
            self._fire_hook("http_response", {"model": self._active_model, "status": "ok"})

            # Prompt cache hit/miss events based on compressor state
            _n_messages = len(self.session.messages)
            _n_cached = sum(1 for i in range(_n_messages) if self._compressor._is_cached(i))
            if _n_cached > 0:
                self._fire_hook("prompt_cache_hit", {"cached_messages": _n_cached, "total_messages": _n_messages})
            else:
                self._fire_hook("prompt_cache_miss", {"total_messages": _n_messages})

            # Mark all messages sent in this request as cached (API has seen them)
            self._compressor.mark_as_cached(set(range(len(self.session.messages))))

            # Accumulate usage
            if stop_event:
                accumulated_usage = TokenUsage(
                    input_tokens=accumulated_usage.input_tokens + stop_event.usage.input_tokens,
                    output_tokens=accumulated_usage.output_tokens + stop_event.usage.output_tokens,
                )

            # Layer 2: Token limit auto-upgrade
            # If the model stopped due to hitting max_tokens, double the limit and retry
            if stop_event is not None and stop_event.stop_reason in ("max_tokens", "length"):
                _upgraded = _current_max_tokens * 2 if _TOKEN_UPGRADE_CAP == 0 else min(_current_max_tokens * 2, _TOKEN_UPGRADE_CAP)
                if _upgraded > _current_max_tokens:
                    logger.warning(
                        "Hit max_tokens limit (%d); upgrading to %d and retrying",
                        _current_max_tokens,
                        _upgraded,
                    )
                    _current_max_tokens = _upgraded
                    continue  # retry this iteration with higher token limit

            # Build native tool call list for parsing
            for call_data in native_tool_calls.values():
                raw_json = "".join(call_data["json_parts"])
                try:
                    parsed_input = json.loads(raw_json) if raw_json else {}
                except json.JSONDecodeError:
                    parsed_input = {}
                native_tool_list.append({
                    "id": call_data["id"],
                    "name": call_data["name"],
                    "input": parsed_input,
                })

            if self._vcr_recorder is not None:
                self._vcr_recorder.record("llm_response", {
                    "text": "".join(text_parts)[:500],
                })

            # 5. Parse tool calls (dual-track)
            response_text = "".join(text_parts)
            parsed_calls = parse_tool_calls(
                response_text=response_text,
                native_tool_calls=native_tool_list if native_tool_list else None,
            )

            # 6. Build assistant message content
            assistant_blocks: list = []
            if response_text:
                assistant_blocks.append(TextBlock(text=response_text))
            for call in parsed_calls:
                assistant_blocks.append(
                    ToolUseBlock(id=call.id, name=call.name, input=call.args)
                )

            # 7. Add assistant message to session
            if assistant_blocks:
                assistant_msg = Message(
                    role="assistant",
                    content=tuple(assistant_blocks),
                )
                self.session = self.session.add_message(assistant_msg)

            # 8. If no tool calls → end turn
            if not parsed_calls:
                break

            # 9. Execute tools via the validate→safety→permission→progress pipeline
            # Collect read-only results that were pre-computed during streaming,
            # and get the list of write calls still needing execution.
            _precomputed_results, _write_pending_calls = await _streaming_executor.collect_results()
            _precomputed_by_id: dict[str, ToolResultBlock] = {r.tool_use_id: r for r in _precomputed_results}

            # Split agent calls from non-agent calls so agents can run in parallel
            agent_calls = [c for c in parsed_calls if c.name == "agent"]
            non_agent_calls = [c for c in parsed_calls if c.name != "agent"]
            for ac in agent_calls:
                self._fire_hook("agent_spawn", {"agent_id": ac.id, "args": str(ac.args)[:200]})

            tool_result_blocks: list[ToolResultBlock] = []

            # Non-agent calls: use pre-computed result if available, else execute normally
            for call in non_agent_calls:
                if call.id in _precomputed_by_id:
                    # Read-only tool already executed concurrently — emit events and reuse result
                    precomputed = _precomputed_by_id[call.id]
                    yield StreamToolExecStart(tool_name=call.name, args_summary=str(call.args)[:80])
                    yield StreamToolExecResult(
                        tool_name=call.name,
                        output=precomputed.content[:200],
                        is_error=precomputed.is_error,
                        metadata=None,
                    )
                    tool_result_blocks.append(precomputed)
                else:
                    async for event in self._execute_tool_with_streaming(call):
                        if isinstance(event, ToolResultBlock):
                            tool_result_blocks.append(event)
                        else:
                            yield event  # StreamToolProgress

            # Agent calls: run in parallel when there are multiple
            if len(agent_calls) > 1:
                async def _run_agent(c):
                    results: list[StreamEvent | ToolResultBlock] = []
                    async for ev in self._execute_tool_with_streaming(c):
                        results.append(ev)
                    return results

                all_agent_results = await asyncio.gather(
                    *[_run_agent(c) for c in agent_calls]
                )
                for idx, result_events in enumerate(all_agent_results):
                    ac = agent_calls[idx]
                    for event in result_events:
                        if isinstance(event, ToolResultBlock):
                            tool_result_blocks.append(event)
                            if event.is_error:
                                self._fire_hook("agent_error", {"agent_id": ac.id, "error": event.content[:200]})
                            else:
                                self._fire_hook("agent_message", {"agent_id": ac.id, "text": event.content[:200]})
                        else:
                            yield event
                    self._fire_hook("agent_complete", {"agent_id": ac.id})
            elif agent_calls:
                # Single agent call — sequential
                for call in agent_calls:
                    async for event in self._execute_tool_with_streaming(call):
                        if isinstance(event, ToolResultBlock):
                            tool_result_blocks.append(event)
                            if event.is_error:
                                self._fire_hook("agent_error", {"agent_id": call.id, "error": event.content[:200]})
                            else:
                                self._fire_hook("agent_message", {"agent_id": call.id, "text": event.content[:200]})
                        else:
                            yield event
                    self._fire_hook("agent_complete", {"agent_id": call.id})

            # Add tool results as user message
            if tool_result_blocks:
                tool_result_msg = Message(
                    role="user",
                    content=tuple(tool_result_blocks),
                )
                self.session = self.session.add_message(tool_result_msg)

            # 10. Loop back for LLM to process results

        # Update session usage
        self.session = self.session.update_usage(accumulated_usage)
        _turn_duration_ms = (time.monotonic() - _turn_start) * 1000
        logger.debug(
            "Turn complete: %d input tokens, %d output tokens",
            accumulated_usage.input_tokens,
            accumulated_usage.output_tokens,
        )
        self._telemetry.trace_turn(
            session_id=getattr(self.session, "session_id", ""),
            model=self._active_model,
            input_tokens=accumulated_usage.input_tokens,
            output_tokens=accumulated_usage.output_tokens,
            duration_ms=_turn_duration_ms,
        )

        # Auto-checkpoint: persist session state after each turn completes
        if self._recovery_checkpoint is not None:
            try:
                self._recovery_checkpoint.save_checkpoint(self.session)
            except Exception as exc:
                logger.debug("Recovery checkpoint save failed: %s", exc)

    async def _execute_tool_with_streaming(
        self, call: ParsedToolCall
    ) -> AsyncIterator[StreamEvent | ToolResultBlock]:
        """Validate → safety → permission → run in thread → yield progress + result."""
        logger.debug("Executing tool: %s", call.name)
        # 1. Look up tool
        tool = self._tool_registry.get(call.name)
        if tool is None:
            logger.warning("Unknown tool requested: %s", call.name)
            self._fire_hook("tool_error", {"tool_name": call.name, "error": "unknown tool"})
            yield ToolResultBlock(
                tool_use_id=call.id,
                content=f"Unknown tool '{call.name}'",
                is_error=True,
            )
            return

        # 2. Validate input
        try:
            validated_args = tool.validate_input(call.args)
        except ValidationError as exc:
            # Format Pydantic validation errors into a readable message
            errors = exc.errors()
            fields = ", ".join(
                f"{'.'.join(str(loc) for loc in e['loc'])}: {e['msg']}"
                for e in errors
            )
            yield ToolResultBlock(
                tool_use_id=call.id,
                content=f"Invalid input for tool '{call.name}': {fields}",
                is_error=True,
            )
            return

        # 3. Safety analysis → effective permission level
        #    For bash-like tools, truly dangerous (blocked) commands are denied
        #    immediately without entering the permission prompt flow.
        if hasattr(tool, "classify") and callable(tool.classify):
            safety = tool.classify(validated_args)
            if safety.is_blocked:
                self._fire_hook("tool_denied", {"tool_name": call.name})
                yield ToolResultBlock(
                    tool_use_id=call.id,
                    content=f"Dangerous command blocked: {'; '.join(safety.reasons)}",
                    is_error=True,
                )
                return

        if tool.is_read_only(validated_args):
            effective = PermissionLevel.READ_ONLY
        elif tool.is_destructive(validated_args):
            effective = PermissionLevel.FULL_ACCESS
        else:
            effective = tool.required_permission

        # 4. Permission check (deny/allow lists still take precedence via authorize)
        outcome = self._permissions.authorize(
            call.name,
            tool.required_permission,
            effective_level=effective,
        )

        if outcome == PermissionOutcome.DENY:
            self._fire_hook("tool_denied", {"tool_name": call.name})
            yield ToolResultBlock(
                tool_use_id=call.id,
                content=f"Permission denied for tool '{call.name}'",
                is_error=True,
            )
            return

        if outcome == PermissionOutcome.NEED_PROMPT:
            # Build a short preview of tool arguments for the permission prompt
            args_preview = json.dumps(validated_args, default=str)[:120]

            # Attempt speculative pre-execution via overlay so the result is
            # ready the moment the user approves.
            spec_executor = None
            try:
                from llm_code.runtime.speculative import SpeculativeExecutor
                import uuid as _uuid
                session_id = f"{call.name}-{_uuid.uuid4().hex[:8]}"
                spec_executor = SpeculativeExecutor(
                    tool=tool,
                    args=validated_args,
                    base_dir=self._context.cwd,
                    session_id=session_id,
                )
                spec_executor.pre_execute()
            except Exception:
                spec_executor = None

            # Yield permission request and wait for user response
            yield StreamPermissionRequest(
                tool_name=call.name,
                args_preview=args_preview,
            )

            loop = asyncio.get_running_loop()
            self._permission_future = loop.create_future()
            try:
                response = await asyncio.wait_for(self._permission_future, timeout=300)
            except asyncio.TimeoutError:
                response = "deny"
                logger.warning("Permission prompt for '%s' timed out (300s), auto-denying", call.name)
            finally:
                self._permission_future = None

            if response in ("allow", "always"):
                if response == "always":
                    # Add to allow list so future calls skip prompting
                    if hasattr(self._permissions, "allow_tool"):
                        self._permissions.allow_tool(call.name)
                if spec_executor is not None:
                    try:
                        spec_executor.confirm()
                    except Exception:
                        pass
                # Fall through to execute the tool normally below
            else:
                # Denied by user
                if spec_executor is not None:
                    try:
                        spec_executor.deny()
                    except Exception:
                        pass
                self._fire_hook("tool_denied", {"tool_name": call.name})
                yield ToolResultBlock(
                    tool_use_id=call.id,
                    content=f"Tool '{call.name}' denied by user",
                    is_error=True,
                )
                return

        # 4b. Create checkpoint before mutating tools
        if self._checkpoint_mgr is not None and not tool.is_read_only(validated_args):
            try:
                self._checkpoint_mgr.create(call.name, validated_args)
            except Exception:
                pass  # Don't block tool execution if checkpoint fails

        # 5. Pre-tool hook
        args = validated_args
        hook_runner = self._hooks
        if hasattr(hook_runner, "pre_tool_use"):
            hook_result = hook_runner.pre_tool_use(call.name, args)
            if hasattr(hook_result, "__await__"):
                hook_result = await hook_result
            if hasattr(hook_result, "denied") and hook_result.denied:
                yield ToolResultBlock(
                    tool_use_id=call.id,
                    content=f"Tool '{call.name}' blocked by hook",
                    is_error=True,
                )
                return
            if isinstance(hook_result, dict):
                args = hook_result

        # 6. Emit tool execution start event
        args_preview = str(args)[:80]
        if self._vcr_recorder is not None:
            self._vcr_recorder.record("tool_call", {"name": call.name, "args": args_preview})
        yield StreamToolExecStart(tool_name=call.name, args_summary=args_preview)
        _tool_start = time.monotonic()

        # 7. Execute in thread pool with asyncio.Queue progress bridge
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()

        def on_progress(p):
            loop.call_soon_threadsafe(queue.put_nowait, p)

        def run_tool():
            result = tool.execute_with_progress(args, on_progress)
            loop.call_soon_threadsafe(queue.put_nowait, None)  # sentinel
            return result

        future = loop.run_in_executor(_TOOL_EXECUTOR, run_tool)

        while True:
            progress = await queue.get()
            if progress is None:
                break
            yield StreamToolProgress(
                tool_name=progress.tool_name,
                message=progress.message,
                percent=progress.percent,
            )

        tool_result = await future
        tool_result = self._budget_tool_result(tool_result, call.id)
        _tool_duration_ms = (time.monotonic() - _tool_start) * 1000
        self._telemetry.trace_tool(
            tool_name=call.name,
            duration_ms=_tool_duration_ms,
            is_error=tool_result.is_error,
        )

        # 7. Post-tool hook
        if hasattr(hook_runner, "post_tool_use"):
            post_result = hook_runner.post_tool_use(call.name, args, tool_result)
            if hasattr(post_result, "__await__"):
                await post_result

        # 8. Emit tool execution result event
        if self._vcr_recorder is not None:
            self._vcr_recorder.record("tool_result", {
                "name": call.name,
                "output": tool_result.output[:200],
                "is_error": tool_result.is_error,
            })
        yield StreamToolExecResult(
            tool_name=call.name,
            output=tool_result.output[:200],
            is_error=tool_result.is_error,
            metadata=tool_result.metadata,
        )

        yield ToolResultBlock(
            tool_use_id=call.id,
            content=tool_result.output,
            is_error=tool_result.is_error,
        )

    def _budget_tool_result(self, result: ToolResult, call_id: str) -> ToolResult:
        """If result is too large, persist to disk and return truncated summary."""
        if len(result.output) <= _MAX_INLINE_RESULT:
            return result

        # Save full output
        cache_dir = self._context.cwd / ".llm-code" / "result_cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path = cache_dir / f"{call_id}.txt"
        cache_path.write_text(result.output, encoding="utf-8")

        # Truncated summary
        summary = (
            result.output[:1000]
            + f"\n\n... [{len(result.output)} chars total, full output saved to {cache_path}. Use read_file to access.]"
        )
        return ToolResult(output=summary, is_error=result.is_error, metadata=result.metadata)
