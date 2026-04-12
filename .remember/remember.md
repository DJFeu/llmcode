# Handoff

## State
Shipped v1.18.2: 6-phase architecture refactor. app.py 3999→1287 (-68%), conversation.py 2374→1907 (-20%). Extracted CommandDispatcher/StreamingHandler/RuntimeInitializer from app.py; PermissionManager/ToolExecutionPipeline from conversation.py. Wired 6 orphan modules (agent_loader, tool_visibility, tool_distill, prompt_snippets, denial_parser, exec_policy). Unified memory (memory_lint→memory_validator, MemoryEntry→KVMemoryEntry), split config.py into config_features.py + config_enterprise.py. Merged enterprise→runtime, streaming→tui. Consolidated swarm/task/cron tools (10→3), added tools/builtin.py. Tag v1.18.2 pushed, GitHub Release published, 7 CI source-inspection tests fixed.

## Next
1. Monitor CI run for v1.18.2 tag — confirm Docker build + PyPI publish workflows succeed
2. Consider Phase 2.1 HookDispatcher (skipped — only 4 lines) or further conversation.py decomposition if 1907 lines still feels large
3. Low-priority: merge voice/sandbox/hida if they become stubs later (currently have real integration, kept as-is)

## Context
- Pre-push hook runs ruff; TYPE_CHECKING imports needed for Verifier/DiagnosticsEngine in task_tools.py
- Source-inspection tests (hasattr/read source) broke after extraction — had to redirect them to new file locations
- Backward-compat shims used everywhere (old files re-export from canonical location) — safe to delete later
- Plan doc at docs/superpowers/plans/2026-04-11-architecture-refactor.md
