"""Core agentic conversation runtime: turn loop with streaming and tool execution."""
from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING, Any, AsyncIterator


from llm_code.logging import get_logger
from llm_code.api.types import (
    Message,
    MessageRequest,
    StreamEvent,
    StreamCompactionDone,
    StreamCompactionStart,
    StreamMessageStop,
    StreamTextDelta,
    StreamServerToolBlock,
    StreamThinkingDelta,
    StreamThinkingSignature,
    StreamToolExecResult,
    StreamToolExecStart,
    StreamToolUseInputDelta,
    StreamToolUseStart,
    TextBlock,
    ThinkingBlock,
    TokenUsage,
    ToolResultBlock,
    ToolUseBlock,
)
from llm_code.api.content_order import validate_assistant_content_order
from llm_code.runtime.compressor import ContextCompressor
from llm_code.runtime.cost_tracker import BudgetExceededError
from llm_code.runtime._retry_tracker import RecentToolCallTracker
from llm_code.runtime.streaming_executor import StreamingToolExecutor
from llm_code.runtime.telemetry import Telemetry, _truncate_for_attribute, get_noop_telemetry
from llm_code.tools.base import ToolResult
from llm_code.tools.parsing import ParsedToolCall, parse_tool_calls

if TYPE_CHECKING:
    from llm_code.runtime.context import ProjectContext
    from llm_code.runtime.permissions import PermissionPolicy
    from llm_code.runtime.prompt import SystemPromptBuilder
    from llm_code.runtime.session import Session
    from llm_code.tools.registry import ToolRegistry


_MAX_CONSECUTIVE_COMPACT_FAILURES = 3


def _build_prompt_preview(messages, max_chars: int = 2000) -> str:
    """Build a short preview of the most recent user/assistant turns."""
    if not messages:
        return ""
    tail = messages[-3:]
    parts: list[str] = []
    per_msg_cap = max(200, max_chars // max(1, len(tail)))
    for msg in tail:
        role = getattr(msg, "role", "?")
        content = getattr(msg, "content", "")
        if isinstance(content, (list, tuple)):
            text_chunks = []
            for block in content:
                txt = getattr(block, "text", None)
                if txt:
                    text_chunks.append(txt)
            content = " ".join(text_chunks)
        text = str(content)[:per_msg_cap]
        parts.append(f"[{role}] {text}")
    out = "\n".join(parts)
    return out[:max_chars]


_THINKING_BOOST_MULTIPLIER = 2


def _apply_thinking_boost(
    runtime: Any,
    *,
    base_budget: int,
    max_budget: int | None = None,
) -> int:
    """If runtime._thinking_boost_active is set, multiply base_budget by
    _THINKING_BOOST_MULTIPLIER (clamped to max_budget) and clear the flag.

    Top-level runtimes without the attribute fall through unchanged.
    """
    if not getattr(runtime, "_thinking_boost_active", False):
        return base_budget
    boosted = base_budget * _THINKING_BOOST_MULTIPLIER
    if max_budget is not None:
        boosted = min(boosted, max_budget)
    runtime._thinking_boost_active = False
    return boosted


def _apply_thinking_budget_cap(
    budget: int,
    *,
    max_output_tokens: int | None,
) -> int:
    """Cap thinking_budget so it cannot eat the entire output token budget.

    Reserves at least half of max_output_tokens for the visible response,
    with a minimum floor of 1024 thinking tokens so reasoning still works
    even on providers with very small output caps.
    """
    if not max_output_tokens:
        return budget
    cap = max(1024, max_output_tokens // 2)
    return min(budget, cap)


def _apply_profile_budget_adjustments(budget: int, profile: Any) -> int:
    """Scale thinking budget by reasoning_effort and cap for small models."""
    if profile is None:
        return budget

    # Scale thinking budget by reasoning effort from profile
    if getattr(profile, "reasoning_effort", ""):
        effort_scale = {
            "low": 0.25,
            "medium": 0.5,
            "high": 1.0,
            "max": 2.0,
        }
        scale = effort_scale.get(profile.reasoning_effort, 1.0)
        budget = int(budget * scale)

    # Auto-downgrade thinking for small models
    if getattr(profile, "is_small_model", False):
        budget = min(budget, 4096)  # Cap at 4K for small models

    return budget


def build_thinking_extra_body(
    thinking_config,
    *,
    is_local: bool = False,
    provider_supports_reasoning: bool = False,
    runtime: Any = None,
    max_output_tokens: int | None = None,
    profile: Any = None,
) -> dict | None:
    """Build extra_body dict for thinking mode configuration.

    - "enabled": explicitly enable thinking (user opted in)
    - "disabled": explicitly disable thinking
    - "adaptive" (default): enable for local models that declare
      reasoning support via ``provider.supports_reasoning()``;
      disable otherwise (vLLM without --enable-reasoning mixes
      thinking text into response); let cloud providers decide.

    The output format is determined by the model profile's
    ``thinking_extra_body_format``:
    - ``"chat_template_kwargs"`` → ``{"chat_template_kwargs": {...}}``
    - ``"anthropic_native"`` → ``{"thinking": {"type": ..., "budget_tokens": ...}}``
    """
    fmt = "chat_template_kwargs"
    if profile is not None:
        fmt = getattr(profile, "thinking_extra_body_format", fmt)

    def _wrap(enabled: bool, budget: int = 0) -> dict:
        if fmt == "anthropic_native":
            if enabled:
                return {"thinking": {"type": "enabled", "budget_tokens": budget}}
            return {"thinking": {"type": "disabled"}}
        # Default: chat_template_kwargs (vLLM / OpenAI-compat)
        if enabled:
            return {"chat_template_kwargs": {"enable_thinking": True, "thinking_budget": budget}}
        return {"chat_template_kwargs": {"enable_thinking": False}}

    mode = thinking_config.mode
    if mode == "enabled":
        budget = thinking_config.budget_tokens
        if is_local:
            budget = max(budget, 131072)
        budget = _apply_thinking_boost(
            runtime,
            base_budget=budget,
            max_budget=131072 if is_local else None,
        )
        budget = _apply_profile_budget_adjustments(budget, profile)
        budget = _apply_thinking_budget_cap(budget, max_output_tokens=max_output_tokens)
        return _wrap(True, budget)
    if mode == "disabled":
        return _wrap(False)
    # adaptive: enable for local models that support reasoning;
    # disable for those that don't (prevents thinking text leak)
    if is_local:
        if provider_supports_reasoning:
            budget = max(thinking_config.budget_tokens, 131072)
            budget = _apply_thinking_boost(
                runtime,
                base_budget=budget,
                max_budget=131072,
            )
            budget = _apply_profile_budget_adjustments(budget, profile)
            budget = _apply_thinking_budget_cap(budget, max_output_tokens=max_output_tokens)
            return _wrap(True, budget)
        return _wrap(False)
    return None


# Thread pool for running blocking tool execution off the event loop
_TOOL_EXECUTOR = ThreadPoolExecutor(max_workers=4)

logger = get_logger(__name__)

# Maximum number of characters to inline in tool results
_MAX_INLINE_RESULT = 2000

# Fallback used when neither provider nor config exposes a context limit and
# the runtime has not yet detected one from an API response.
_DEFAULT_MAX_INPUT_TOKENS = 200_000


def _record_token_usage(
    runtime: "ConversationRuntime", *, used_tokens: int, max_tokens: int
) -> None:
    """Store cumulative input tokens + model context limit on the runtime so
    the context_window_monitor builtin hook can read them via getattr.

    Called after every LLM stream completes.
    """
    runtime._last_input_tokens = int(used_tokens)
    runtime._max_input_tokens = int(max_tokens)


def _merge_hook_extra_output(result: ToolResult, outcome) -> ToolResult:
    """Append HookOutcome.extra_output to a ToolResult.output (immutable update).

    Keeps the original ToolResult instance when nothing to append, so callers
    can compare by identity in tests and avoid an unneeded allocation.
    """
    extra = getattr(outcome, "extra_output", "") or ""
    if not extra:
        return result
    return ToolResult(
        output=result.output + extra,
        is_error=result.is_error,
        metadata=result.metadata,
    )


@dataclasses.dataclass(frozen=True)
class TurnSummary:
    iterations: int
    total_usage: TokenUsage


# ---------------------------------------------------------------------------
# ConversationRuntime
# ---------------------------------------------------------------------------

class ConversationRuntime:
    """Agentic loop that drives LLM turns, tool execution, and session updates."""

    # Class-level defaults so builtin hooks can read these via getattr even
    # before the runtime has processed its first stream.
    _last_input_tokens: int = 0
    _max_input_tokens: int = 0

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
        lsp_manager: Any = None,
        typed_memory_store: Any = None,
        dialogs: Any = None,
    ) -> None:
        self._provider = provider
        self._last_input_tokens: int = 0
        self._max_input_tokens: int = 0
        self._tool_registry = tool_registry
        self._permissions = permission_policy
        self._hooks = hook_runner
        from llm_code.runtime.hook_dispatcher import HookDispatcher
        self._hook_dispatcher = HookDispatcher(hook_runner)
        self._thinking_boost_active = False
        # Register opt-in builtin Python hooks (config.builtin_hooks.enabled).
        try:
            _builtin_cfg = getattr(config, "builtin_hooks", None)
            _enabled_names = tuple(getattr(_builtin_cfg, "enabled", ()) or ())
            if _enabled_names and hook_runner is not None:
                from llm_code.runtime.builtin_hooks import register_named
                register_named(hook_runner, _enabled_names)
        except Exception as _exc:  # pragma: no cover - defensive
            logger.warning("builtin hooks registration failed: %s", _exc)
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
        # Task 4: register frontmatter-declared hooks from every loaded skill
        if skills is not None and hook_runner is not None:
            try:
                from llm_code.runtime.frontmatter_hooks import register_skillset_hooks
                _all_skills = tuple(getattr(skills, "auto_skills", ()) or ()) + tuple(
                    getattr(skills, "command_skills", ()) or ()
                )
                if _all_skills:
                    register_skillset_hooks(_all_skills, hook_runner)
            except Exception as _exc:  # pragma: no cover - defensive
                logger.warning("frontmatter hook registration failed: %s", _exc)
        self._skill_router = None
        if skills and skills.auto_skills:
            from llm_code.runtime.skill_router import SkillRouter
            from llm_code.runtime.config import SkillRouterConfig
            router_cfg = getattr(config, "skill_router", SkillRouterConfig())
            self._skill_router = SkillRouter(
                skills=skills.auto_skills,
                config=router_cfg,
                provider=provider,
                model=getattr(config, "model", ""),
            )
        self._mcp_manager = mcp_manager
        # Pending skill-scoped MCP spawns — built from skill.mcp_servers.
        # Actual spawn is deferred to the first run_turn so we have an
        # event loop + approval sink in place. Failures are logged and
        # never block runtime startup.
        self._pending_skill_mcp_spawns: list[tuple[str, str]] = []
        self._skill_mcp_spawned: bool = False
        try:
            if skills is not None:
                _all_for_mcp = tuple(getattr(skills, "auto_skills", ()) or ()) + tuple(
                    getattr(skills, "command_skills", ()) or ()
                )
                for _sk in _all_for_mcp:
                    for _srv in getattr(_sk, "mcp_servers", ()) or ():
                        self._pending_skill_mcp_spawns.append((_sk.name, _srv))
        except Exception as _exc:  # pragma: no cover - defensive
            logger.warning("skill MCP spawn queue build failed: %s", _exc)
        # Permission manager — centralises MCP approval, session allowlists,
        # and interactive permission prompts.
        from llm_code.runtime.permission_manager import PermissionManager
        self._perm_mgr = PermissionManager(
            permission_policy, session, context=context,
        )
        self._memory_store = memory_store
        self._task_manager = task_manager
        self._project_index = project_index
        self._lsp_manager = lsp_manager
        self._typed_memory = typed_memory_store
        self._dialogs = dialogs
        # Conversation DB for cross-session FTS5 search
        self._conv_db = None
        try:
            from llm_code.runtime.conversation_db import ConversationDB
            self._conv_db = ConversationDB()
            _cwd_str = str(context.cwd) if context and hasattr(context, "cwd") else ""
            self._conv_db.ensure_conversation(
                conv_id=session.id,
                name=session.id[:8],
                model=getattr(config, "model", ""),
                project_path=_cwd_str,
            )
        except Exception:
            pass
        # Harness Engine — unified quality controls
        from llm_code.harness.engine import HarnessEngine
        from llm_code.harness.config import HarnessConfig
        from llm_code.harness.templates import detect_template, default_controls
        cwd = Path(self._context.cwd) if self._context and hasattr(self._context, "cwd") else Path.cwd()
        harness_cfg = getattr(config, "harness", HarnessConfig())
        if harness_cfg.template == "auto" and not harness_cfg.controls:
            template = detect_template(cwd)
            resolved_controls = default_controls(template)
            harness_cfg = HarnessConfig(template=template, controls=resolved_controls)
        elif harness_cfg.template == "auto":
            template = detect_template(cwd)
            harness_cfg = HarnessConfig(template=template, controls=harness_cfg.controls)
        self._harness = HarnessEngine(config=harness_cfg, cwd=cwd)
        self._harness.lsp_manager = lsp_manager
        if hasattr(config, "auto_commit") and not config.auto_commit:
            self._harness.disable("auto_commit")
        if hasattr(config, "lsp_auto_diagnose") and not config.lsp_auto_diagnose:
            self._harness.disable("lsp_diagnose")
        # _permission_future now lives on self._perm_mgr
        # Tool execution pipeline
        from llm_code.runtime.tool_pipeline import ToolExecutionPipeline
        self._tool_pipeline = ToolExecutionPipeline(self)
        self._has_attempted_reactive_compact = False
        self._consecutive_compact_failures: int = 0
        self._compaction_in_flight: bool = False
        from llm_code.runtime.query_profiler import QueryProfiler
        self._query_profiler = QueryProfiler()
        self._consecutive_failures: int = 0
        self._compressor = ContextCompressor()
        self._active_model: str = getattr(config, "model", "")
        # Resolve the model profile — declarative capability + behaviour
        # spec that replaces scattered hardcoded model adaptations.
        from llm_code.runtime.model_profile import get_profile
        self._model_profile = get_profile(self._active_model)
        # Wave2-1c: consecutive empty assistant responses. We fire the
        # empty_assistant_response hook every time, inject a nudge
        # user-message after 2 (to prod the model into actually
        # answering), and raise RuntimeError after 3 to prevent a
        # tight loop from burning a whole retry budget on nothing.
        self._consecutive_empty_responses: int = 0
        # Wave2-1c: track the last-seen context pressure bucket (low/
        # mid/high) so we only fire the context_pressure hook once
        # per crossing instead of every turn past the threshold.
        self._last_context_pressure_bucket: str = "low"
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

    # ------------------------------------------------------------------
    # Backward-compatible properties delegating to HarnessEngine
    # ------------------------------------------------------------------

    @property
    def plan_mode(self) -> bool:
        return self._harness.plan_mode

    @plan_mode.setter
    def plan_mode(self, value: bool) -> None:
        self._harness.plan_mode = value

    @property
    def analysis_context(self) -> str | None:
        return self._harness.analysis_context

    @analysis_context.setter
    def analysis_context(self, value: str | None) -> None:
        self._harness.analysis_context = value

    # Back-compat properties delegating to PermissionManager so existing
    # tests and TUI code that access these attributes directly keep working.

    @property
    def _mcp_approved_servers(self) -> set[str]:
        return self._perm_mgr._mcp_approved_servers

    @_mcp_approved_servers.setter
    def _mcp_approved_servers(self, value: set[str]) -> None:
        self._perm_mgr._mcp_approved_servers = value

    @property
    def _mcp_approval_pending(self) -> bool:
        return self._perm_mgr._mcp_approval_pending

    @_mcp_approval_pending.setter
    def _mcp_approval_pending(self, value: bool) -> None:
        self._perm_mgr._mcp_approval_pending = value

    @property
    def _session_allowed_tools(self) -> set[str]:
        return self._perm_mgr._session_allowed_tools

    @_session_allowed_tools.setter
    def _session_allowed_tools(self, value: set[str]) -> None:
        self._perm_mgr._session_allowed_tools = value

    @property
    def _session_allowed_exact(self) -> set[tuple[str, str]]:
        return self._perm_mgr._session_allowed_exact

    @_session_allowed_exact.setter
    def _session_allowed_exact(self, value: set[tuple[str, str]]) -> None:
        self._perm_mgr._session_allowed_exact = value

    @property
    def _session_allowed_prefixes(self) -> set[str]:
        return self._perm_mgr._session_allowed_prefixes

    @_session_allowed_prefixes.setter
    def _session_allowed_prefixes(self, value: set[str]) -> None:
        self._perm_mgr._session_allowed_prefixes = value

    @property
    def _session_allowed_path_roots(self) -> set[str]:
        return self._perm_mgr._session_allowed_path_roots

    @_session_allowed_path_roots.setter
    def _session_allowed_path_roots(self, value: set[str]) -> None:
        self._perm_mgr._session_allowed_path_roots = value

    @property
    def dialogs(self) -> Any:
        """The ``Dialogs`` backend for this runtime.

        Returns the backend set at construction time (TextualDialogs in
        the TUI, HeadlessDialogs in CLI/pipe mode, ScriptedDialogs in
        tests). Falls back to a default HeadlessDialogs if none was
        provided, so callers never need to None-check.
        """
        if self._dialogs is not None:
            return self._dialogs
        from llm_code.tui.dialogs.headless import HeadlessDialogs
        self._dialogs = HeadlessDialogs()
        return self._dialogs

    def _db_log(self, role: str, content: str, input_tokens: int = 0, output_tokens: int = 0) -> None:
        """Log a message to ConversationDB for cross-session FTS5 search."""
        if self._conv_db is None:
            return
        try:
            from datetime import datetime, timezone
            self._conv_db.log_message(
                conversation_id=self.session.id,
                role=role,
                content=content[:10_000],  # cap at 10K chars
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                created_at=datetime.now(timezone.utc).isoformat(),
            )
        except Exception:
            pass

    def _db_log_thinking(self, content: str, signature: str = "") -> None:
        """Log an assistant thinking trace to ConversationDB.

        Wave2-1a P5: reasoning traces are indexed in FTS5 alongside
        visible assistant text so ``db.search(query, content_type=
        "thinking")`` can surface them across sessions. Silently
        degrades if the DB is unavailable or cap-limited.
        """
        if self._conv_db is None or not content:
            return
        try:
            from datetime import datetime, timezone
            self._conv_db.log_thinking(
                conversation_id=self.session.id,
                content=content[:10_000],
                signature=signature,
                created_at=datetime.now(timezone.utc).isoformat(),
            )
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Delegated to PermissionManager
    # ------------------------------------------------------------------

    def set_mcp_approval_callback(self, callback: Any) -> None:
        self._perm_mgr.set_mcp_approval_callback(callback)

    def set_mcp_event_sink(self, sink: Any) -> None:
        self._perm_mgr.set_mcp_event_sink(sink)

    def send_mcp_approval_response(self, response: str) -> None:
        self._perm_mgr.send_mcp_approval_response(response)

    async def request_mcp_approval(self, request: Any) -> bool:
        return await self._perm_mgr.request_mcp_approval(request)


    def _fire_hook(self, event: str, context: dict | None = None) -> None:
        """Fire a hook event via the extracted HookDispatcher.

        Kept as a thin delegator so existing call sites inside this module
        (pre_compact, session_compact, prompt_submit, http_fallback…) stay
        unchanged. See ``llm_code.runtime.hook_dispatcher`` for the actual
        guard logic.
        """
        self._hook_dispatcher.fire(event, context)

    def _find_last_tool_result(self, tool_name: str) -> str | None:
        """Find the most recent successful ToolResultBlock for a tool.

        Walks session messages in reverse to find the last tool result
        matching *tool_name* that is not an error.  Returns the content
        string, or None if not found.
        """
        for msg in reversed(self.session.messages):
            if msg.role != "user":
                continue
            for block in msg.content:
                if (
                    isinstance(block, ToolResultBlock)
                    and not block.is_error
                    and block.content
                ):
                    # Check if this result corresponds to the tool by
                    # looking at the preceding assistant message's
                    # tool_use block with the same tool_use_id.
                    # Shortcut: just check if the content looks like
                    # a search result (non-trivial length).
                    if len(block.content) > 50:
                        return block.content
        return None

    def _compact_with_todo_preserve(
        self,
        max_tokens: int,
        *,
        reason: str,
        max_result_chars: int | None = None,
    ) -> None:
        """Wave2-4: Run ContextCompressor with phase-split hooks + todo snapshot.

        Fires ``pre_compact`` (with the incomplete-task snapshot and
        estimated before-token count), runs the compressor, then fires
        ``post_compact`` with the after-token count. ``session_compact``
        still fires alongside ``pre_compact`` so existing hook
        configurations keep working unchanged.

        All four in-tree compact call sites route through this helper so
        observers get uniform visibility regardless of which trigger
        (prompt_too_long / proactive / api_reported / post_tool) fired
        the compaction.
        """
        # Local import to avoid module-level cycles in runtime package.
        from llm_code.runtime.todo_preserver import snapshot_incomplete_tasks

        snapshot = snapshot_incomplete_tasks(self._task_manager)
        before_tokens = self.session.estimated_tokens()
        payload = {
            "reason": reason,
            "before_tokens": before_tokens,
            "target_tokens": max_tokens,
            "preserved_todos": tuple(
                {"id": s.task_id, "status": s.status, "title": s.title} for s in snapshot
            ),
        }
        self._fire_hook("pre_compact", payload)
        # Back-compat: existing hook configs still listen for this event.
        self._fire_hook("session_compact", {"reason": reason})

        compressor = (
            ContextCompressor(max_result_chars=max_result_chars)
            if max_result_chars is not None
            else ContextCompressor()
        )
        self.session = compressor.compress(self.session, max_tokens)

        self._fire_hook(
            "post_compact",
            {
                "reason": reason,
                "before_tokens": before_tokens,
                "after_tokens": self.session.estimated_tokens(),
                "preserved_todos": payload["preserved_todos"],
            },
        )

    def is_session_allowed(
        self, tool_name: str, args_preview: str, validated_args: dict | None = None,
    ) -> bool:
        return self._perm_mgr.is_session_allowed(tool_name, args_preview, validated_args)

    def record_permission_choice(
        self,
        choice: str,
        tool_name: str,
        args_preview: str,
        validated_args: dict | None = None,
    ) -> None:
        self._perm_mgr.record_permission_choice(choice, tool_name, args_preview, validated_args)

    def send_permission_response(self, response: str, *, edited_args: dict | None = None) -> None:
        self._perm_mgr.send_permission_response(response, edited_args=edited_args)

    async def _spawn_pending_skill_mcp_servers(self) -> None:
        """Spawn on-demand MCP servers declared by loaded skills.

        Runs at most once per session (idempotent). Each server is started
        under ``skill:<skill_name>`` so auto-cleanup happens at session end
        via ``McpServerManager.stop_all``. Failures are logged and swallowed
        so skill MCP issues never break the runtime.
        """
        if self._skill_mcp_spawned:
            return
        self._skill_mcp_spawned = True
        if not self._pending_skill_mcp_spawns:
            return
        mcp_manager = self._mcp_manager
        if mcp_manager is None or not hasattr(mcp_manager, "start_server"):
            return
        mcp_cfg = getattr(self._config, "mcp", None)
        on_demand = getattr(mcp_cfg, "on_demand", {}) or {}
        from llm_code.mcp.types import McpServerConfig

        for skill_name, server_name in self._pending_skill_mcp_spawns:
            raw = on_demand.get(server_name)
            if raw is None:
                logger.warning(
                    "skill %s declares MCP server '%s' not in mcp.on_demand — skipping",
                    skill_name,
                    server_name,
                )
                continue
            try:
                if isinstance(raw, McpServerConfig):
                    cfg = raw
                elif isinstance(raw, dict):
                    cfg = McpServerConfig(
                        command=raw.get("command"),
                        args=tuple(raw.get("args", ()) or ()),
                        env=raw.get("env"),
                        transport_type=raw.get("transport_type", "stdio"),
                        url=raw.get("url"),
                        headers=raw.get("headers"),
                    )
                else:
                    logger.warning(
                        "skill %s MCP server '%s' has invalid config shape",
                        skill_name,
                        server_name,
                    )
                    continue
                await mcp_manager.start_server(
                    server_name,
                    cfg,
                    owner_agent_id=f"skill:{skill_name}",
                    approval_callback=self.request_mcp_approval,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "skill %s MCP server '%s' spawn failed: %s",
                    skill_name,
                    server_name,
                    exc,
                )

    async def run_turn(self, user_input: str, images: list | None = None, active_skill_content: str | None = None) -> AsyncIterator[StreamEvent]:
        """Run one user turn wrapped in an agent.turn span for telemetry nesting."""
        session_id = getattr(self.session, "session_id", "") or getattr(self.session, "id", "")
        _turn_attrs = {
            "session.id": session_id,
            "model": getattr(self, "_active_model", "") or getattr(self._config, "model", ""),
        }
        with self._telemetry.span("agent.turn", **_turn_attrs):
            async for event in self._run_turn_body(user_input, images, active_skill_content):
                yield event

    async def run_one_turn(
        self,
        user_input: str,
        images: list | None = None,
        active_skill_content: str | None = None,
    ) -> list:
        """Drive one turn to completion and collect emitted events.

        Convenience helper for tests and non-streaming callers (like
        ``run_quick_mode``) that want to exercise the full runner path
        but don't need incremental streaming.
        """
        events: list = []
        async for event in self.run_turn(
            user_input, images=images, active_skill_content=active_skill_content
        ):
            events.append(event)
        return events

    async def _run_turn_body(self, user_input: str, images: list | None = None, active_skill_content: str | None = None) -> AsyncIterator[StreamEvent]:
        """Run one user turn (may involve multiple LLM calls for tool use)."""
        logger.debug("Starting turn: %s", user_input[:80])
        # First-turn lazy spawn of skill-declared on-demand MCP servers.
        try:
            await self._spawn_pending_skill_mcp_servers()
        except Exception as _exc:  # pragma: no cover - defensive
            logger.warning("skill MCP spawn failed: %s", _exc)
        _turn_start = time.monotonic()
        self._fire_hook("prompt_submit", {"text": user_input[:200]})
        if self._hooks is not None and hasattr(self._hooks, "fire_python"):
            _ps_ctx = {
                "prompt": user_input,
                "session_id": getattr(self._context, "session_id", ""),
            }
            self._hooks.fire_python("prompt_submit", _ps_ctx)
            if _ps_ctx.get("thinking_requested"):
                self._thinking_boost_active = True
        # Keyword-driven action detection (Feature 6, opt-in via keywords.enabled).
        try:
            _kw_cfg = getattr(self._config, "keywords", None)
            if _kw_cfg is not None and getattr(_kw_cfg, "enabled", False):
                from llm_code.runtime.keyword_actions import detect_action
                _action = detect_action(user_input)
                if _action:
                    logger.info("keyword_action detected: %s", _action)
                    self._fire_hook(
                        "keyword_action",
                        {"action": _action, "message": user_input[:200]},
                    )
        except Exception as _exc:  # pragma: no cover - defensive
            logger.debug("keyword_action detection failed: %s", _exc)
        if self._vcr_recorder is not None:
            self._vcr_recorder.record("user_input", {"text": user_input})
        # 1. Add user message to session (with optional images)
        content_blocks: list = [TextBlock(text=user_input)]
        if images:
            content_blocks.extend(images)
        user_msg = Message(role="user", content=tuple(content_blocks))
        self.session = self.session.add_message(user_msg)
        self._db_log("user", user_input)

        accumulated_usage = TokenUsage(input_tokens=0, output_tokens=0)
        self._has_attempted_reactive_compact = False
        # ``self._force_xml_mode`` is sticky both within a turn
        # (iteration 2 honors iteration 1's fallback) AND across
        # sessions (seeded from the persistent server_capabilities
        # cache so the 14s native-rejection round-trip is paid ONCE
        # per server+model combo, EVER).
        if not hasattr(self, "_force_xml_mode"):
            # The model profile is the primary source of truth: if the
            # profile says force_xml_tools, we skip native tool calling
            # entirely (no 14s error-retry on first request).
            self._force_xml_mode = self._model_profile.force_xml_tools
            if self._force_xml_mode:
                logger.debug(
                    "model profile %r: force_xml_tools=True, skipping "
                    "native tool calling",
                    self._model_profile.name,
                )
            else:
                # Fall back to the on-disk server capabilities cache
                # for models without a profile declaration.
                try:
                    from llm_code.runtime.server_capabilities import (
                        load_native_tools_support,
                    )
                    _cached = load_native_tools_support(
                        base_url=getattr(self._config, "provider_base_url", "") or "",
                        model=self._active_model,
                    )
                    if _cached is False:
                        self._force_xml_mode = True
                        logger.debug(
                            "server_capabilities cache: %s (%s) marked "
                            "as not supporting native tool calling",
                            getattr(self._config, "provider_base_url", ""),
                            self._active_model,
                        )
                except Exception:
                    pass
        # Token limit auto-upgrade state: reset each turn, doubles on max_tokens stop
        _current_max_tokens: int = self._config.max_tokens
        # Determine if the model is locally-hosted (unlimited token upgrades).
        # Profile is authoritative; URL-based heuristic is the fallback for
        # models without an explicit profile.
        _base_url = getattr(self._config, "provider_base_url", "") or ""
        _is_local = self._model_profile.is_local or self._model_profile.unlimited_token_upgrade
        if not _is_local:
            # Fallback: detect self-hosted models by URL pattern
            _is_local = (
                any(h in _base_url for h in ("localhost", "127.0.0.1", "0.0.0.0", "192.168.", "10.", "172."))
                or _base_url.startswith("http://")  # non-HTTPS = likely self-hosted
                or self._active_model.startswith("/")  # path-based model name = vLLM
            )
        _TOKEN_UPGRADE_CAP = 0 if _is_local else 65536  # 0 means unlimited

        # Determine effective context limit for proactive compaction
        _context_limit = self._config.compact_after_tokens
        # Auto-detect model context window and profile (query /v1/models once)
        if not hasattr(self, "_detected_context_window"):
            self._detected_context_window = 0
            try:
                import httpx
                resp = httpx.get(f"{_base_url.rstrip('/v1').rstrip('/')}/v1/models", timeout=3.0)
                if resp.status_code == 200:
                    for m in resp.json().get("data", []):
                        mml = m.get("max_model_len", 0)
                        if mml > 0:
                            self._detected_context_window = mml
                            break
            except Exception:
                pass
            # Profile auto-discovery: if current profile is default,
            # try to resolve a better one from the provider's model list.
            if self._model_profile.name in ("(default)", ""):
                from llm_code.runtime.model_profile import probe_provider_profile
                _discovered = probe_provider_profile(_base_url, self._active_model)
                if _discovered is not None:
                    self._model_profile = _discovered
                    # Re-evaluate is_local with the new profile
                    _is_local = self._model_profile.is_local or self._model_profile.unlimited_token_upgrade
                    if not _is_local:
                        _is_local = (
                            any(h in _base_url for h in ("localhost", "127.0.0.1", "0.0.0.0", "192.168.", "10.", "172."))
                            or _base_url.startswith("http://")
                            or self._active_model.startswith("/")
                        )
                    _TOKEN_UPGRADE_CAP = 0 if _is_local else 65536
        if self._detected_context_window > 0:
            # Use 70% of model's context window as compaction threshold
            _context_limit = min(_context_limit, int(self._detected_context_window * 0.7))

        _prev_output_tokens = 0
        _continuation_count = 0
        _retry_tracker = RecentToolCallTracker()
        _force_text_next_iteration = False

        for _iteration in range(self._config.max_turn_iterations):
            # Proactive context compaction: compress before hitting model limit
            est_tokens = self.session.estimated_tokens()

            # Wave2-1c: fire context_pressure hook on bucket transitions
            # (low → mid at 70%, mid → high at 85%) BEFORE the
            # compaction trigger at 100%. The buckets keep the event
            # from firing every turn past the threshold — observers
            # only see it on the actual crossing.
            _pressure_ratio = est_tokens / max(_context_limit, 1)
            if _pressure_ratio >= 0.85:
                _new_bucket = "high"
            elif _pressure_ratio >= 0.70:
                _new_bucket = "mid"
            else:
                _new_bucket = "low"
            if _new_bucket != self._last_context_pressure_bucket:
                # Only fire on ascending transitions — dropping back
                # after a compaction is not a pressure event.
                if _new_bucket in ("mid", "high") and _new_bucket != "low":
                    self._fire_hook(
                        "context_pressure",
                        {
                            "bucket": _new_bucket,
                            "ratio": round(_pressure_ratio, 3),
                            "est_tokens": est_tokens,
                            "limit": _context_limit,
                        },
                    )
                    logger.info(
                        "context_pressure %s: %d/%d tokens (%.0f%%)",
                        _new_bucket, est_tokens, _context_limit, _pressure_ratio * 100,
                    )
                self._last_context_pressure_bucket = _new_bucket

            if est_tokens > _context_limit:
                logger.info(
                    "Proactive compaction: %d tokens > %d limit",
                    est_tokens, _context_limit,
                )
                self._compact_with_todo_preserve(
                    int(_context_limit * 0.6), reason="proactive",
                )
                # Compaction dropped us back under pressure; reset bucket
                # so the next ascending crossing re-fires the hook.
                self._last_context_pressure_bucket = "low"

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
            # Read self._force_xml_mode fresh each iteration — the
            # auto-fallback at the except block below sets it, and
            # subsequent iterations in the same turn must honor it.
            use_native = (
                getattr(self._provider, "supports_native_tools", lambda: True)()
                and not self._force_xml_mode
            )

            # Deferred tool loading: when a manager is present, split tools into
            # visible and deferred; inject a hint into the system prompt.
            _deferred_hint: str | None = None
            if self._deferred_tool_manager is not None:
                all_defs = list(self._tool_registry.definitions(
                    allowed=allowed_tool_names, model=self._active_model,
                ))
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
                    model=self._active_model,
                )

            # Intent-based tool visibility for local models
            if _is_local and tool_defs:
                try:
                    from llm_code.tools.tool_visibility import visible_tools_for_turn
                    all_names = frozenset(t.name for t in tool_defs)
                    visible_names = visible_tools_for_turn(all_names, user_input)
                    if visible_names is not None:
                        tool_defs = tuple(t for t in tool_defs if t.name in visible_names)
                except Exception:
                    pass  # Fall through to full tool set

            # Distill tool descriptions for small local models
            if _is_local and tool_defs and getattr(self._model_profile, "is_small_model", False):
                try:
                    from llm_code.tools.tool_distill import distill_definitions
                    tool_defs = distill_definitions(tool_defs, compact=True)
                except Exception:
                    pass

            # Forced-text mode for local models: after tool results,
            # strip ALL tools so the model is physically unable to call
            # tools and must generate a text response instead.
            if _force_text_next_iteration:
                tool_defs = ()
                _force_text_next_iteration = False
                logger.debug("Forced-text mode: stripped tools for this iteration")

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
            # Enrich with typed memory (4-type taxonomy)
            if self._typed_memory is not None:
                try:
                    for entry in self._typed_memory.list_all()[:20]:
                        if _memory_entries is None:
                            _memory_entries = {}
                        _memory_entries[f"[{entry.memory_type.value}] {entry.name}"] = entry.content[:500]
                except Exception:
                    pass

            # Route auto-skills based on user intent
            _routed: tuple = ()
            if self._skill_router is not None:
                _routed = tuple(await self._skill_router.route_async(user_input))
            # Low confidence when the match was produced by Tier C LLM classifier
            _routed_low_confidence = (
                self._skill_router is not None
                and getattr(self._skill_router, "last_tier_used", "") == "c"
            )
            # Track routed skills on runtime so TUI can show them in status
            self._last_routed_skills = tuple(s.name for s in _routed) if _routed else ()
            if _routed:
                self._fire_hook("skill_routed", {
                    "skills": list(self._last_routed_skills),
                })

            system_prompt = self._prompt_builder.build(
                self._context,
                tools=tool_defs,
                native_tools=use_native,
                skills=self._skills,
                active_skill_content=active_skill_content,
                mcp_instructions=_mcp_instructions,
                memory_entries=_memory_entries,
                task_manager=self._task_manager,
                project_index=self._project_index,
                routed_skills=_routed,
                routed_skills_low_confidence=_routed_low_confidence,
                is_local_model=_is_local,
                model_name=self._active_model,
            )
            if _deferred_hint:
                system_prompt = system_prompt + "\n\n" + _deferred_hint

            # Inject harness guide context (repo map, analysis, etc.)
            for injection in self._harness.pre_turn():
                if injection:
                    system_prompt = system_prompt + "\n\n" + injection

            # Anti-recursion suffix for sub-agents
            _subagent_suffix = getattr(self, "_subagent_system_suffix", None)
            if _subagent_suffix:
                system_prompt = system_prompt + _subagent_suffix

            self._fire_hook("prompt_compile", {"prompt_length": len(system_prompt), "tool_count": len(tool_defs)})

            # 3. Create request and stream
            request = MessageRequest(
                model=self._active_model,
                messages=self.session.messages,
                system=system_prompt,
                tools=tool_defs if use_native else (),
                max_tokens=_current_max_tokens,
                temperature=(
                    self._model_profile.default_temperature
                    if self._model_profile.default_temperature >= 0
                    else self._config.temperature
                ),
                extra_body=build_thinking_extra_body(
                    self._config.thinking,
                    is_local=_is_local,
                    provider_supports_reasoning=self._provider.supports_reasoning(),
                    runtime=self,
                    max_output_tokens=_current_max_tokens,
                    profile=self._model_profile,
                ) if not use_native else None,
            )

            if self._vcr_recorder is not None:
                self._vcr_recorder.record("llm_request", {
                    "model": request.model,
                    "max_tokens": request.max_tokens,
                })

            # Error recovery: tool choice fallback + reactive compact
            self._fire_hook("http_request", {"model": self._active_model, "url": getattr(self._config, "provider_base_url", "")})
            _prompt_preview = _build_prompt_preview(self.session.messages)
            # Open the llm.completion span and KEEP IT OPEN across the entire
            # stream-consume loop so output-side attributes (preview, output
            # tokens, finish_reason) can be set just before the span closes.
            # ExitStack lets us tie its lifetime to a finally block that
            # survives the recovery branches below.
            _llm_span_stack = contextlib.ExitStack()
            _llm_span = _llm_span_stack.enter_context(
                self._telemetry.trace_llm_completion(
                    session_id=getattr(self.session, "session_id", "") or getattr(self.session, "id", ""),
                    model=self._active_model,
                    prompt_preview=_prompt_preview,
                    provider=getattr(self._config, "provider", "") or "",
                )
            )
            _llm_span_closed = False

            def _close_llm_span_with_error(_e: BaseException) -> None:
                nonlocal _llm_span_closed
                if _llm_span_closed:
                    return
                _llm_span_closed = True
                try:
                    _llm_span_stack.__exit__(type(_e), _e, _e.__traceback__)
                except Exception:
                    pass

            def _close_llm_span_ok() -> None:
                nonlocal _llm_span_closed
                if _llm_span_closed:
                    return
                _llm_span_closed = True
                try:
                    _llm_span_stack.close()
                except Exception:
                    pass

            try:
                stream = await self._provider.stream_message(request)
            except Exception as exc:
                _exc_str = str(exc)
                self._fire_hook("http_error", {"error": _exc_str[:200], "model": self._active_model})
                # ORDER MATTERS: the tool-call-parser fallback branch
                # MUST be checked BEFORE the wave2-3 is_retryable
                # short-circuit. PR #41 marks the tool-call-parser
                # error as ``is_retryable=False`` to skip the
                # _post_with_retry retry loop (that was burning 30s
                # on 3 exponential retries before the fallback
                # could fire). But the same error HAS a recovery
                # path right here: rebuild the request without
                # tools=[...] and retry in XML tag mode. If the
                # is_retryable short-circuit ran first, this
                # recoverable error would surface to the user as
                # visible text instead — observed in a field report
                # after PR #41 merged: "Error: auto tool choice
                # requires --enable-auto-tool-choice and
                # --tool-call-parser to be set" became the visible
                # assistant reply.
                if "tool-call-parser" in _exc_str or "tool choice" in _exc_str.lower():
                    logger.debug("Server does not support native tool calling; falling back to XML tag mode")
                    self._fire_hook("http_fallback", {"reason": "xml_mode", "model": self._active_model})
                    self._force_xml_mode = True
                    # Write the result to the persistent cache so
                    # the NEXT session for this server+model skips
                    # the 14s native-rejection round-trip entirely.
                    # Best-effort: cache failures are logged at DEBUG
                    # inside the module and never raise.
                    try:
                        from llm_code.runtime.server_capabilities import (
                            mark_native_tools_unsupported,
                        )
                        mark_native_tools_unsupported(
                            base_url=getattr(self._config, "provider_base_url", "") or "",
                            model=self._active_model,
                        )
                    except Exception:
                        pass
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
                        routed_skills=_routed,
                        routed_skills_low_confidence=_routed_low_confidence,
                        is_local_model=_is_local,
                        model_name=self._active_model,
                    )
                    request = MessageRequest(
                        model=self._active_model,
                        messages=self.session.messages,
                        system=system_prompt,
                        tools=(),
                        max_tokens=_current_max_tokens,
                        temperature=(
                            self._model_profile.default_temperature
                            if self._model_profile.default_temperature >= 0
                            else self._config.temperature
                        ),
                        extra_body=build_thinking_extra_body(
                            self._config.thinking,
                            is_local=_is_local,
                            provider_supports_reasoning=self._provider.supports_reasoning(),
                            runtime=self,
                            max_output_tokens=_current_max_tokens,
                            profile=self._model_profile,
                        ),
                    )
                    try:
                        stream = await self._provider.stream_message(request)
                    except Exception as retry_exc:
                        # XML fallback retry itself failed — close the open
                        # llm.completion span before propagating, otherwise
                        # the span leaks (no try/finally on the outer block).
                        logger.error("XML fallback retry failed: %s", retry_exc)
                        _close_llm_span_with_error(retry_exc)
                        raise
                elif getattr(exc, "is_retryable", None) is False:
                    # Wave2-3 Fix 1 (re-added after the tool-call-parser
                    # fallback above): genuinely non-retryable errors
                    # (401 auth, 404 model-not-found, etc.) propagate
                    # immediately so the 3-strike retry budget isn't
                    # wasted on something that can never succeed.
                    self._fire_hook(
                        "http_non_retryable",
                        {"error": _exc_str[:200], "model": self._active_model},
                    )
                    logger.error("Non-retryable provider error; not retrying: %s", exc)
                    _close_llm_span_with_error(exc)
                    raise
                elif (
                    ("413" in _exc_str or "prompt too long" in _exc_str.lower())
                    and not self._has_attempted_reactive_compact
                    and self._consecutive_compact_failures < _MAX_CONSECUTIVE_COMPACT_FAILURES
                ):
                    logger.warning("Prompt too long; compacting context and retrying")
                    self._has_attempted_reactive_compact = True
                    self._consecutive_compact_failures += 1
                    # helper fires pre_compact + session_compact + post_compact
                    self._compact_with_todo_preserve(
                        self._config.compact_after_tokens // 2,
                        reason="prompt_too_long",
                    )
                    _close_llm_span_with_error(exc)
                    continue  # retry this iteration of the turn loop
                elif (
                    ("413" in _exc_str or "prompt too long" in _exc_str.lower())
                    and self._consecutive_compact_failures >= _MAX_CONSECUTIVE_COMPACT_FAILURES
                ):
                    logger.error(
                        "Circuit breaker: %d consecutive compact failures, not retrying",
                        self._consecutive_compact_failures,
                    )
                    _close_llm_span_with_error(exc)
                    raise
                else:
                    # Layer 3: model fallback — track consecutive provider
                    # errors and walk the declarative FallbackChain when a
                    # model has exhausted its retry budget.
                    self._consecutive_failures += 1
                    from llm_code.runtime.fallback import FallbackChain
                    _chain = FallbackChain.from_routing(
                        getattr(self._config, "model_routing", None)
                        or type("_Empty", (), {"fallback": "", "fallbacks": ()})()
                    )
                    _next_model = _chain.next(self._active_model) if _chain else None
                    if _next_model:
                        # Still have retries remaining before switching — retry same model
                        if self._consecutive_failures < 3:
                            self._fire_hook("http_retry", {"attempt": self._consecutive_failures, "model": self._active_model})
                            logger.warning(
                                "Provider error (attempt %d/3): %s",
                                self._consecutive_failures,
                                exc,
                            )
                            _close_llm_span_with_error(exc)
                            continue  # retry this iteration
                        # 3rd consecutive failure: walk one step down the chain.
                        self._fire_hook(
                            "http_fallback",
                            {
                                "reason": "consecutive_failures",
                                "from": self._active_model,
                                "to": _next_model,
                            },
                        )
                        logger.warning(
                            "3 consecutive provider errors; switching from %s → %s (chain: %s)",
                            self._active_model,
                            _next_model,
                            list(_chain),
                        )
                        self._active_model = _next_model
                        # Wave2-3 Fix 2: keep cost_tracker in sync so token
                        # usage after fallback is attributed to the correct
                        # model instead of the (failed) primary.
                        if self._cost_tracker is not None:
                            try:
                                self._cost_tracker.model = _next_model
                            except Exception:
                                logger.debug("cost_tracker.model sync skipped", exc_info=True)
                        self._consecutive_failures = 0
                        _close_llm_span_with_error(exc)
                        continue  # retry with fallback model
                    logger.error("Provider stream error: %s", exc)
                    _close_llm_span_with_error(exc)
                    raise

            # 4. Collect events and buffers
            text_parts: list[str] = []
            # Wave2-1a P3: accumulate provider thinking deltas so we
            # can prepend them as a ThinkingBlock on the assembled
            # assistant message. Streaming never carries a signature
            # today (Anthropic signature arrives on block_stop events
            thinking_parts: list[str] = []
            thinking_signature: str = ""
            server_tool_blocks: list = []  # ServerToolUse/ResultBlock for round-trip
            native_tool_calls: dict[str, dict] = {}  # id -> {id, name, json_parts}
            native_tool_list: list[dict] = []
            stop_event: StreamMessageStop | None = None

            # StreamingToolExecutor: starts read-only tools in background while streaming
            _streaming_executor = StreamingToolExecutor(self._tool_registry, self._permissions)
            _current_streaming_tool_id: str | None = None

            try:
                async for event in stream:
                    # Yield streaming events to caller
                    yield event

                    if isinstance(event, StreamTextDelta):
                        text_parts.append(event.text)
                    elif isinstance(event, StreamThinkingDelta):
                        thinking_parts.append(event.text)
                    elif isinstance(event, StreamThinkingSignature):
                        thinking_signature = event.signature
                    elif isinstance(event, StreamServerToolBlock):
                        server_tool_blocks.append(event.block)
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
            except BaseException as _stream_exc:
                _close_llm_span_with_error(_stream_exc)
                raise
            else:
                # Enrich llm.completion span with output-side attributes
                # before closing it. This is the Issue 1 fix: previously the
                # span closed before any output was observed and Langfuse
                # showed blank completion fields.
                try:
                    if _llm_span is not None and not _llm_span_closed:
                        _completion_text = "".join(text_parts)
                        _llm_span.set_attribute(
                            "llm.completion.preview",
                            _truncate_for_attribute(_completion_text),
                        )
                        if stop_event is not None:
                            _llm_span.set_attribute(
                                "llm.tokens.output",
                                int(stop_event.usage.output_tokens),
                            )
                            _llm_span.set_attribute(
                                "llm.tokens.input",
                                int(stop_event.usage.input_tokens),
                            )
                            _llm_span.set_attribute(
                                "llm.tokens.total",
                                int(stop_event.usage.input_tokens + stop_event.usage.output_tokens),
                            )
                            _llm_span.set_attribute(
                                "llm.finish_reason",
                                str(stop_event.stop_reason or ""),
                            )
                except Exception:
                    pass
                _close_llm_span_ok()

            # Reset consecutive failure counters on successful stream
            self._consecutive_failures = 0
            self._consecutive_compact_failures = 0
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
                _record_token_usage(
                    self,
                    used_tokens=accumulated_usage.input_tokens,
                    max_tokens=getattr(self._provider, "max_input_tokens", 0)
                    or getattr(self._config, "max_input_tokens", 0)
                    or getattr(self, "_detected_context_window", 0)
                    or _DEFAULT_MAX_INPUT_TOKENS,
                )

                # Per-model query profiler (Task 3)
                try:
                    self._query_profiler.record(
                        model=self._active_model, usage_block=stop_event.usage
                    )
                except Exception:  # pragma: no cover - defensive
                    pass

                # Auto-compaction (Task 1): fire after each model turn when
                # the context crosses the configured trigger threshold.
                _compaction_cfg = getattr(self._config, "compaction", None)
                if (
                    _compaction_cfg is not None
                    and getattr(_compaction_cfg, "auto_enabled", False)
                    and not self._compaction_in_flight
                ):
                    try:
                        from llm_code.runtime.auto_compact import (
                            CompactionThresholds,
                            should_compact,
                            compact_messages,
                        )
                        _thr_cfg = _compaction_cfg.thresholds
                        _thresholds = CompactionThresholds(
                            trigger_pct=_thr_cfg.trigger_pct,
                            min_messages=_thr_cfg.min_messages,
                            min_text_blocks=_thr_cfg.min_text_blocks,
                            target_pct=_thr_cfg.target_pct,
                        )
                        _used = stop_event.usage.input_tokens
                        _max_t = getattr(self._config, "compact_after_tokens", 0) or 128_000
                        if should_compact(
                            self.session.messages, _used, _max_t, _thresholds
                        ):
                            self._compaction_in_flight = True
                            yield StreamCompactionStart(
                                used_tokens=_used, max_tokens=_max_t,
                            )
                            try:
                                _before = len(self.session.messages)
                                _target = int(_max_t * _thresholds.target_pct)
                                self.session = compact_messages(
                                    self.session, target_tokens=_target,
                                )
                                yield StreamCompactionDone(
                                    before_messages=_before,
                                    after_messages=len(self.session.messages),
                                )
                            finally:
                                self._compaction_in_flight = False
                    except Exception as _exc:  # pragma: no cover - defensive
                        logger.warning("auto-compaction failed: %s", _exc)
                        self._compaction_in_flight = False

                # Use ACTUAL API token count for compaction (not estimated)
                actual_input = stop_event.usage.input_tokens
                if actual_input > _context_limit:
                    logger.info(
                        "API-reported compaction: %d actual tokens > %d limit",
                        actual_input, _context_limit,
                    )
                    self._compact_with_todo_preserve(
                        int(_context_limit * 0.5),
                        reason="api_reported",
                        max_result_chars=1000,
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
            # Pass the registry's known tool names so the bare
            # ``<NAME>JSON</NAME>`` variant (Qwen3.5 vLLM on some
            # chat templates) only matches real tools — without
            # this guard, an HTML-ish fragment like ``<p>{"a":1}</p>``
            # would be misclassified as a tool call.
            _known_tool_names = {t.name for t in self._tool_registry.all_tools()}
            parsed_calls = parse_tool_calls(
                response_text=response_text,
                native_tool_calls=native_tool_list if native_tool_list else None,
                known_tool_names=_known_tool_names,
            )

            # 6. Build assistant message content
            # Wave2-1a P3: thinking blocks must come FIRST within an
            # assistant message — this is enforced both by Anthropic's
            # API contract (signed thinking must precede text/tool_use
            # for the round-trip to validate) and by our own pure
            # validator from P1. We merge all streamed thinking deltas
            # into a single ThinkingBlock because consecutive same-type
            # blocks are semantically equivalent and a single block is
            # cheaper to persist.
            assistant_blocks: list = []
            if thinking_parts:
                assistant_blocks.append(
                    ThinkingBlock(
                        content="".join(thinking_parts),
                        signature=thinking_signature,
                    )
                )
            if response_text:
                assistant_blocks.append(TextBlock(text=response_text))
            # Server-side tool blocks (web search, etc.) — round-trip with signatures
            for stb in server_tool_blocks:
                assistant_blocks.append(stb)
            for call in parsed_calls:
                assistant_blocks.append(
                    ToolUseBlock(id=call.id, name=call.name, input=call.args)
                )

            # 7. Add assistant message to session
            if assistant_blocks:
                # Wave2-1c: a non-empty assistant turn resets the
                # consecutive-empty counter. Even a lone ToolUseBlock
                # counts as productive output.
                self._consecutive_empty_responses = 0
                # Wave2-1a P3: defence-in-depth — even though the
                # prepend logic above makes the order trivially correct,
                # running the validator here catches any future refactor
                # that accidentally reorders the block list.
                validate_assistant_content_order(tuple(assistant_blocks))
                assistant_msg = Message(
                    role="assistant",
                    content=tuple(assistant_blocks),
                )
                self.session = self.session.add_message(assistant_msg)
                # Log assistant text to DB for cross-session search
                _assistant_text = "".join(t for t in text_parts if t)
                if _assistant_text:
                    _out_tok = stop_event.usage.output_tokens if stop_event else 0
                    self._db_log("assistant", _assistant_text, output_tokens=_out_tok)
                # Wave2-1a P5: log thinking trace as a separate
                # searchable row so FTS5 search can filter by
                # content_type. Signature is empty for current
                # providers; a future AnthropicProvider will emit
                # non-empty signatures that survive the round-trip.
                if thinking_parts:
                    self._db_log_thinking(
                        content="".join(thinking_parts),
                        signature=thinking_signature,
                    )
            else:
                # Wave2-1c: empty assistant response (no text, no
                # tool calls). Count, hook, nudge on 2nd in a row,
                # abort on 3rd so a degenerate provider state cannot
                # burn the entire turn budget on nothing.
                self._consecutive_empty_responses += 1
                self._fire_hook(
                    "empty_assistant_response",
                    {
                        "consecutive": self._consecutive_empty_responses,
                        "model": self._active_model,
                    },
                )
                logger.warning(
                    "empty assistant response #%d for model %s",
                    self._consecutive_empty_responses, self._active_model,
                )
                if self._consecutive_empty_responses >= 3:
                    raise RuntimeError(
                        "3 consecutive empty assistant responses; aborting turn "
                        "to prevent runaway loop. Check provider health or "
                        "reduce thinking budget."
                    )
                if self._consecutive_empty_responses >= 2:
                    # Inject a nudge user message to prod the model
                    # into actually responding on the next attempt.
                    nudge = Message(
                        role="user",
                        content=(
                            TextBlock(text=(
                                "[system nudge] Your previous response was empty. "
                                "Please provide an answer, ask a clarifying question, "
                                "or call a tool."
                            )),
                        ),
                    )
                    self.session = self.session.add_message(nudge)

            # 8. Diminishing returns detection
            # Only check when model produced text without tool calls (pure text continuation).
            # Tool calls = productive work, never penalized.
            _dr_cfg = getattr(self._config, "diminishing_returns", None)
            if _dr_cfg and _dr_cfg.enabled and stop_event and not parsed_calls:
                _current_output = accumulated_usage.output_tokens
                _delta = _current_output - _prev_output_tokens
                _prev_output_tokens = _current_output
                _continuation_count += 1
                if (
                    _continuation_count >= _dr_cfg.min_continuations
                    and _delta < _dr_cfg.min_delta_tokens
                ):
                    logger.info(
                        "Diminishing returns: iteration %d, delta %d tokens < %d threshold",
                        _continuation_count, _delta, _dr_cfg.min_delta_tokens,
                    )
                    _msg_template = getattr(
                        _dr_cfg,
                        "auto_stop_message",
                        "\n[Auto-stopped: diminishing returns — iteration {iteration}, {delta} new tokens]",
                    )
                    try:
                        _msg = _msg_template.format(
                            iteration=_continuation_count, delta=_delta
                        )
                    except (KeyError, IndexError, ValueError):
                        _msg = _msg_template
                    yield StreamTextDelta(text=_msg)
                    break
            elif parsed_calls:
                # Reset counter when model is actively using tools
                _continuation_count = 0

            # 9. If no tool calls → end turn
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
            # Set to True when the idempotent-retry tracker detects
            # the model is stuck calling the same tool with the same
            # args. We drop out of the inner dispatch loop AND the
            # outer turn-iteration loop so the turn actually ends —
            # previously the inner ``continue`` only skipped the one
            # offending call, the model got another iteration, saw
            # the "Aborted" error block, and re-emitted the same
            # call, burning the full max_turn_iterations budget.
            _turn_aborted_by_retry_loop = False

            # Non-agent calls: use pre-computed result if available, else execute normally
            for call in non_agent_calls:
                if _retry_tracker.is_idempotent_retry(call.name, call.args):
                    logger.warning(
                        "Retry loop detected for %s; recovering with existing tool results",
                        call.name,
                    )
                    # Instead of aborting, recover: find the previous
                    # successful tool result in session history and
                    # present it as a text response.  This handles
                    # local models (Qwen/DeepSeek) that re-emit the
                    # same tool call instead of reading the result.
                    _previous_result = self._find_last_tool_result(call.name)
                    if _previous_result:
                        yield StreamTextDelta(text=_previous_result)
                    else:
                        yield StreamTextDelta(
                            text=(
                                f"\n\n⚠ The model attempted to call "
                                f"{call.name!r} again with the same arguments. "
                                f"No previous result found to recover from.\n"
                            ),
                        )
                    _turn_aborted_by_retry_loop = True
                    break  # Stop dispatching any more calls from this batch
                _retry_tracker.record(call.name, call.args)
                if call.id in _precomputed_by_id:
                    # Read-only tool already executed concurrently — emit events and reuse result
                    precomputed = _precomputed_by_id[call.id]
                    yield StreamToolExecStart(
                        tool_name=call.name,
                        args_summary=repr(call.args),
                        tool_id=call.id,
                    )
                    yield StreamToolExecResult(
                        tool_name=call.name,
                        output=precomputed.content[:200],
                        is_error=precomputed.is_error,
                        metadata=None,
                        tool_id=call.id,
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

                # Local model forced-text mode: after receiving tool
                # results, some quantized models (Qwen INT4, DeepSeek)
                # either end immediately or re-call the same tool
                # instead of producing a text answer.  Setting this
                # flag strips tools from the NEXT iteration so the
                # model is physically forced to generate text.
                if _is_local:
                    _force_text_next_iteration = True

                # Compact after tool results to prevent context overflow
                est = self.session.estimated_tokens()
                if est > _context_limit:
                    logger.info(
                        "Post-tool compaction: %d tokens > %d limit",
                        est, _context_limit,
                    )
                    self._compact_with_todo_preserve(
                        int(_context_limit * 0.6), reason="post_tool",
                    )

            # Fast exit if the idempotent-retry tracker detected a
            # loop. We still record the error tool_result_block and
            # add it to the session so the model's message history
            # is consistent, but we don't give the model another
            # iteration to re-emit the same failing call.
            if _turn_aborted_by_retry_loop:
                if tool_result_blocks:
                    tool_msg = Message(
                        role="user",
                        content=tuple(tool_result_blocks),
                    )
                    self.session = self.session.add_message(tool_msg)
                break  # Exit the outer turn-iteration loop

            # 10. Loop back for LLM to process results

        # Update session usage
        self.session = self.session.update_usage(accumulated_usage)
        _turn_duration_ms = (time.monotonic() - _turn_start) * 1000
        logger.debug(
            "Turn complete: %d input tokens, %d output tokens",
            accumulated_usage.input_tokens,
            accumulated_usage.output_tokens,
        )
        # Telemetry: per-turn data now lives on the wrapping agent.turn span
        # (enriched via trace_llm_completion children). Legacy trace_turn is
        # kept on Telemetry for backwards compatibility but not called here.

        # Auto-checkpoint: persist session state after each turn completes
        if self._recovery_checkpoint is not None:
            try:
                self._recovery_checkpoint.save_checkpoint(
                    self.session, cost_tracker=self._cost_tracker,
                )
            except Exception as exc:
                logger.debug("Recovery checkpoint save failed: %s", exc)

    async def _execute_tool_with_streaming(
        self, call: ParsedToolCall,
    ) -> AsyncIterator[StreamEvent | ToolResultBlock]:
        """Delegate to ToolExecutionPipeline."""
        async for event in self._tool_pipeline.execute_with_streaming(call):
            yield event

    def _budget_tool_result(self, result: ToolResult, call_id: str) -> ToolResult:
        """Delegate to ToolExecutionPipeline."""
        return self._tool_pipeline.budget_result(result, call_id)
