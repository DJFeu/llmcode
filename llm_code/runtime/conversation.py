"""Core agentic conversation runtime: turn loop with streaming and tool execution."""
from __future__ import annotations

import asyncio
import dataclasses
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING, Any, AsyncIterator

from pydantic import ValidationError

from llm_code.logging import get_logger
from llm_code.api.types import (
    Message,
    MessageRequest,
    StreamEvent,
    StreamCompactionDone,
    StreamCompactionStart,
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


def build_thinking_extra_body(
    thinking_config,
    *,
    is_local: bool = False,
    provider_supports_reasoning: bool = False,
) -> dict | None:
    """Build extra_body dict for thinking mode configuration.

    - "enabled": explicitly enable thinking (user opted in)
    - "disabled": explicitly disable thinking
    - "adaptive" (default): enable for local models that declare
      reasoning support via ``provider.supports_reasoning()``;
      disable otherwise (vLLM without --enable-reasoning mixes
      thinking text into response); let cloud providers decide.
    """
    mode = thinking_config.mode
    if mode == "enabled":
        budget = thinking_config.budget_tokens
        if is_local:
            budget = max(budget, 131072)
        return {
            "chat_template_kwargs": {
                "enable_thinking": True,
                "thinking_budget": budget,
            }
        }
    if mode == "disabled":
        return {"chat_template_kwargs": {"enable_thinking": False}}
    # adaptive: enable for local models that support reasoning;
    # disable for those that don't (prevents thinking text leak)
    if is_local:
        if provider_supports_reasoning:
            budget = max(thinking_config.budget_tokens, 131072)
            return {
                "chat_template_kwargs": {
                    "enable_thinking": True,
                    "thinking_budget": budget,
                }
            }
        return {"chat_template_kwargs": {"enable_thinking": False}}
    return None


# Thread pool for running blocking tool execution off the event loop
_TOOL_EXECUTOR = ThreadPoolExecutor(max_workers=4)

logger = get_logger(__name__)

# Maximum number of characters to inline in tool results
_MAX_INLINE_RESULT = 2000


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
        lsp_manager: Any = None,
        typed_memory_store: Any = None,
    ) -> None:
        self._provider = provider
        self._tool_registry = tool_registry
        self._permissions = permission_policy
        self._hooks = hook_runner
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
        # Callback for MCP approval requests from non-root agents.
        # When None, request_mcp_approval auto-denies (CLI-safe default).
        self._mcp_approval_callback: Any = None
        # Event sink for out-of-band MCP approval prompts. TUI installs this
        # via set_mcp_event_sink to receive StreamMCPApprovalRequest events
        # raised from request_mcp_approval (called by McpServerManager).
        self._mcp_event_sink: Any = None
        self._mcp_approval_future: "asyncio.Future[str] | None" = None
        self._mcp_approval_pending: bool = False
        # In-session allowlist for "always allow this server" responses.
        self._mcp_approved_servers: set[str] = set()
        self._memory_store = memory_store
        self._task_manager = task_manager
        self._project_index = project_index
        self._lsp_manager = lsp_manager
        self._typed_memory = typed_memory_store
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
        self._permission_future: asyncio.Future[str] | None = None
        # Per-session in-memory permission allowlist for "always" responses.
        # Tools added to _session_allowed_tools skip the prompt entirely.
        # _session_allowed_exact contains (tool_name, args_preview) tuples for
        # "always allow this exact" choices. _session_allowed_prefixes contains
        # bash-command prefixes (e.g. "git ") that auto-allow.
        self._session_allowed_tools: set[str] = set()
        self._session_allowed_exact: set[tuple[str, str]] = set()
        self._session_allowed_prefixes: set[str] = set()
        self._session_allowed_path_roots: set[str] = set()
        self._has_attempted_reactive_compact = False
        self._compaction_in_flight: bool = False
        from llm_code.runtime.query_profiler import QueryProfiler
        self._query_profiler = QueryProfiler()
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

    def set_mcp_approval_callback(self, callback: Any) -> None:
        """Install a callback used to approve non-root MCP spawns."""
        self._mcp_approval_callback = callback

    def set_mcp_event_sink(self, sink: Any) -> None:
        """Install a sink callable that receives out-of-band MCP events.

        The sink is called as ``sink(event)`` where event is a
        :class:`StreamMCPApprovalRequest`. It must mount a UI widget and
        eventually resolve the approval by calling
        :meth:`send_mcp_approval_response`.
        """
        self._mcp_event_sink = sink

    def send_mcp_approval_response(self, response: str) -> None:
        """Resolve a pending MCP approval prompt with 'allow', 'always', or 'deny'.

        Safe to call from Textual ``on_key`` since the runtime and the widget
        live on the same asyncio event loop.
        """
        fut = self._mcp_approval_future
        if fut is not None and not fut.done():
            fut.set_result(response)

    async def request_mcp_approval(self, request: Any) -> bool:
        """Ask the attached UI to approve *request*; default-deny if none.

        Behavior:
          * If a custom callback was installed via ``set_mcp_approval_callback``,
            defer to it (backwards compatible).
          * Otherwise yield a StreamMCPApprovalRequest to the installed event
            sink and suspend on a future until the user responds.
          * If no sink is installed, default-deny (CLI-safe).
          * A server approved with "always" is cached per-session and
            auto-approved on subsequent requests.
        """
        # Legacy callback path (tests + custom integrations).
        callback = self._mcp_approval_callback
        if callback is not None:
            try:
                return bool(await callback(request))
            except Exception:  # noqa: BLE001
                return False

        # Extract a server name from the request shape (MCPApprovalRequest uses
        # server_names tuple; stream event uses a single server_name).
        server_name = ""
        owner_agent_id = ""
        description = ""
        if hasattr(request, "server_names") and request.server_names:
            server_name = request.server_names[0]
        elif hasattr(request, "server_name"):
            server_name = request.server_name
        if hasattr(request, "agent_name"):
            owner_agent_id = request.agent_name
        elif hasattr(request, "owner_agent_id"):
            owner_agent_id = request.owner_agent_id
        if hasattr(request, "reason"):
            description = request.reason or ""

        # In-session allowlist short-circuit.
        if server_name and server_name in self._mcp_approved_servers:
            return True

        sink = self._mcp_event_sink
        if sink is None:
            return False

        from llm_code.api.types import StreamMCPApprovalRequest
        event = StreamMCPApprovalRequest(
            server_name=server_name,
            owner_agent_id=owner_agent_id,
            command="",
            description=description,
        )
        try:
            sink(event)
        except Exception:  # noqa: BLE001
            logger.warning("mcp approval sink raised", exc_info=True)
            return False

        loop = asyncio.get_running_loop()
        self._mcp_approval_future = loop.create_future()
        self._mcp_approval_pending = True
        try:
            response = await asyncio.wait_for(
                self._mcp_approval_future, timeout=120,
            )
        except asyncio.TimeoutError:
            response = "deny"
        finally:
            self._mcp_approval_future = None
            self._mcp_approval_pending = False

        if response in ("allow", "always"):
            if response == "always" and server_name:
                self._mcp_approved_servers.add(server_name)
            return True
        return False

    def _fire_hook(self, event: str, context: dict | None = None) -> None:
        """Fire a hook event if the hook runner supports the generic fire() method."""
        if hasattr(self._hooks, "fire"):
            self._hooks.fire(event, context or {})

    def is_session_allowed(
        self, tool_name: str, args_preview: str, validated_args: dict | None = None,
    ) -> bool:
        """Return True if a tool call is pre-approved by the in-session allowlist."""
        if tool_name in self._session_allowed_tools:
            return True
        if (tool_name, args_preview) in self._session_allowed_exact:
            return True
        if tool_name == "bash" and validated_args is not None:
            cmd = str(validated_args.get("command", "")).strip()
            for prefix in self._session_allowed_prefixes:
                if cmd.startswith(prefix):
                    return True
        if tool_name in ("edit_file", "write_file", "multi_edit") and validated_args is not None:
            path = str(validated_args.get("path") or validated_args.get("file_path") or "")
            for root in self._session_allowed_path_roots:
                if path.startswith(root):
                    return True
        return False

    def record_permission_choice(
        self,
        choice: str,
        tool_name: str,
        args_preview: str,
        validated_args: dict | None = None,
    ) -> None:
        """Persist an 'always' permission choice in the in-session allowlist.

        ``choice`` is one of: 'always_kind', 'always_exact'.
        For bash 'always_kind' also records the first command token as a prefix
        rule. For file edits 'always_kind' records the workspace root.
        """
        if choice == "always_kind":
            # File edit tools record a path-root scope rather than blanket allow,
            # so an "always" choice in /work/proj does not also allow edits in /other.
            if tool_name not in ("edit_file", "write_file", "multi_edit"):
                self._session_allowed_tools.add(tool_name)
            if tool_name == "bash" and validated_args is not None:
                cmd = str(validated_args.get("command", "")).strip()
                first = cmd.split()[0] if cmd else ""
                if first:
                    self._session_allowed_prefixes.add(first + " ")
            if tool_name in ("edit_file", "write_file", "multi_edit"):
                try:
                    self._session_allowed_path_roots.add(str(self._context.cwd))
                except Exception:
                    pass
        elif choice == "always_exact":
            self._session_allowed_exact.add((tool_name, args_preview))

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
        force_xml = getattr(self, "_force_xml_mode", False)
        # Token limit auto-upgrade state: reset each turn, doubles on max_tokens stop
        _current_max_tokens: int = self._config.max_tokens
        # Local models (localhost/private network) have no cost concern — no cap
        _base_url = getattr(self._config, "provider_base_url", "") or ""
        # Detect self-hosted models: private IPs, localhost, or model paths (e.g. /models/Qwen...)
        _is_local = (
            any(h in _base_url for h in ("localhost", "127.0.0.1", "0.0.0.0", "192.168.", "10.", "172."))
            or _base_url.startswith("http://")  # non-HTTPS = likely self-hosted
            or self._active_model.startswith("/")  # path-based model name = vLLM
        )
        _TOKEN_UPGRADE_CAP = 0 if _is_local else 65536  # 0 means unlimited

        # Determine effective context limit for proactive compaction
        _context_limit = self._config.compact_after_tokens
        # Auto-detect model context window (query /v1/models once)
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
        if self._detected_context_window > 0:
            # Use 70% of model's context window as compaction threshold
            _context_limit = min(_context_limit, int(self._detected_context_window * 0.7))

        _prev_output_tokens = 0
        _continuation_count = 0

        for _iteration in range(self._config.max_turn_iterations):
            # Proactive context compaction: compress before hitting model limit
            est_tokens = self.session.estimated_tokens()
            if est_tokens > _context_limit:
                logger.info(
                    "Proactive compaction: %d tokens > %d limit",
                    est_tokens, _context_limit,
                )
                _compressor = ContextCompressor()
                self.session = _compressor.compress(
                    self.session, int(_context_limit * 0.6),
                )

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
                is_local_model=_is_local,
                model_name=self._active_model,
            )
            if _deferred_hint:
                system_prompt = system_prompt + "\n\n" + _deferred_hint

            # Inject harness guide context (repo map, analysis, etc.)
            for injection in self._harness.pre_turn():
                if injection:
                    system_prompt = system_prompt + "\n\n" + injection

            self._fire_hook("prompt_compile", {"prompt_length": len(system_prompt), "tool_count": len(tool_defs)})

            # 3. Create request and stream
            request = MessageRequest(
                model=self._active_model,
                messages=self.session.messages,
                system=system_prompt,
                tools=tool_defs if use_native else (),
                max_tokens=_current_max_tokens,
                temperature=self._config.temperature,
                extra_body=build_thinking_extra_body(
                    self._config.thinking,
                    is_local=_is_local,
                    provider_supports_reasoning=self._provider.supports_reasoning(),
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
            _llm_span_cm = self._telemetry.trace_llm_completion(
                session_id=getattr(self.session, "session_id", "") or getattr(self.session, "id", ""),
                model=self._active_model,
                prompt_preview=_prompt_preview,
                provider=getattr(self._config, "provider", "") or "",
            )
            try:
                with _llm_span_cm:
                    stream = await self._provider.stream_message(request)
            except Exception as exc:
                _exc_str = str(exc)
                self._fire_hook("http_error", {"error": _exc_str[:200], "model": self._active_model})
                # Auto-fallback: if native tool calling is not supported by server
                if "tool-call-parser" in _exc_str or "tool choice" in _exc_str.lower():
                    logger.debug("Server does not support native tool calling; falling back to XML tag mode")
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
                        routed_skills=_routed,
                        is_local_model=_is_local,
                        model_name=self._active_model,
                    )
                    request = MessageRequest(
                        model=self._active_model,
                        messages=self.session.messages,
                        system=system_prompt,
                        tools=(),
                        max_tokens=_current_max_tokens,
                        temperature=self._config.temperature,
                        extra_body=build_thinking_extra_body(
                            self._config.thinking,
                            is_local=_is_local,
                            provider_supports_reasoning=self._provider.supports_reasoning(),
                        ),
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
                    _compressor = ContextCompressor(max_result_chars=1000)
                    self.session = _compressor.compress(
                        self.session, int(_context_limit * 0.5),
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
                # Log assistant text to DB for cross-session search
                _assistant_text = "".join(t for t in text_parts if t)
                if _assistant_text:
                    _out_tok = stop_event.usage.output_tokens if stop_event else 0
                    self._db_log("assistant", _assistant_text, output_tokens=_out_tok)

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

            # Non-agent calls: use pre-computed result if available, else execute normally
            for call in non_agent_calls:
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

                # Compact after tool results to prevent context overflow
                est = self.session.estimated_tokens()
                if est > _context_limit:
                    logger.info(
                        "Post-tool compaction: %d tokens > %d limit",
                        est, _context_limit,
                    )
                    _compressor = ContextCompressor()
                    self.session = _compressor.compress(
                        self.session, int(_context_limit * 0.6),
                    )

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

        # 4a. Plan mode — deny write tools (via harness)
        denial_msg = self._harness.check_pre_tool(call.name)
        if denial_msg:
            self._fire_hook("tool_denied", {"tool_name": call.name})
            yield ToolResultBlock(
                tool_use_id=call.id,
                content=denial_msg,
                is_error=True,
            )
            return

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

            # In-session allowlist short-circuit (user previously chose
            # "always" for this tool / prefix / exact-args).
            if not self.is_session_allowed(call.name, args_preview, validated_args):
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

                # Extract diff preview + pending file list from speculative
                # pre-execution so the permission widget can show the user the
                # actual change before they approve.
                spec_diff_lines: tuple[str, ...] = ()
                spec_pending_files: tuple[str, ...] = ()
                if spec_executor is not None:
                    try:
                        spec_pending_files = tuple(
                            str(p) for p in spec_executor.list_pending_changes()
                        )
                    except Exception:
                        spec_pending_files = ()
                    try:
                        result_obj = spec_executor._result
                        if result_obj is not None and result_obj.metadata:
                            hunks = result_obj.metadata.get("diff") or []
                            collected: list[str] = []
                            for hunk in hunks:
                                old_start = hunk.get("old_start", 0)
                                old_lines = hunk.get("old_lines", 0)
                                new_start = hunk.get("new_start", 0)
                                new_lines = hunk.get("new_lines", 0)
                                collected.append(
                                    f"@@ -{old_start},{old_lines} "
                                    f"+{new_start},{new_lines} @@"
                                )
                                for line in hunk.get("lines", []):
                                    collected.append(line)
                            spec_diff_lines = tuple(collected)
                    except Exception:
                        spec_diff_lines = ()

                # Yield permission request and wait for user response
                yield StreamPermissionRequest(
                    tool_name=call.name,
                    args_preview=args_preview,
                    diff_lines=spec_diff_lines,
                    pending_files=spec_pending_files,
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

                if response in ("allow", "always", "always_kind", "always_exact"):
                    if response in ("always", "always_kind"):
                        # Add to allow list so future calls skip prompting
                        if hasattr(self._permissions, "allow_tool"):
                            self._permissions.allow_tool(call.name)
                        self.record_permission_choice(
                            "always_kind", call.name, args_preview, validated_args,
                        )
                    elif response == "always_exact":
                        self.record_permission_choice(
                            "always_exact", call.name, args_preview, validated_args,
                        )
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
        # Use repr() so the formatter can ast.literal_eval it back to a dict.
        # Don't truncate here — render_tool_args() handles truncation per-tool.
        args_preview = repr(args)
        if self._vcr_recorder is not None:
            self._vcr_recorder.record("tool_call", {"name": call.name, "args": args_preview})
        yield StreamToolExecStart(
            tool_name=call.name, args_summary=args_preview, tool_id=call.id,
        )
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

        # 7b. Run harness sensors (auto-commit, LSP diagnose, code rules)
        try:
            findings = await self._harness.post_tool(
                tool_name=call.name,
                file_path=args.get("file_path") or args.get("path", ""),
                is_error=tool_result.is_error,
            )
            for finding in findings:
                if finding.severity == "error":
                    yield StreamToolProgress(
                        tool_name=finding.sensor,
                        message=f"{finding.sensor} found issues in {Path(finding.file_path).name}:\n{finding.message}",
                        percent=None,
                    )
        except Exception:
            pass  # Never block tool flow for harness failure

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
            tool_id=call.id,
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
        cache_dir = self._context.cwd / ".llmcode" / "result_cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path = cache_dir / f"{call_id}.txt"
        cache_path.write_text(result.output, encoding="utf-8")

        # Truncated summary
        summary = (
            result.output[:1000]
            + f"\n\n... [{len(result.output)} chars total, full output saved to {cache_path}. Use read_file to access.]"
        )
        return ToolResult(output=summary, is_error=result.is_error, metadata=result.metadata)
