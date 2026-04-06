# TODO — 下一個 Session 繼續

> Updated: 2026-04-06
> Context: v1.0.2 — 所有 TODO 項目全部完成

## 測試狀態

**3424 passed, 0 failed, 8 skipped**

## 未提交的變更（跨兩個 session 累積）

**功能改動：**
- `llm_code/runtime/prompt.py` — auto skills 加「不要 spawn agent」指引
- `llm_code/runtime/session.py` — estimated_tokens() 加 4000 overhead + tiktoken optional
- `llm_code/runtime/secret_scanner.py` — +6 patterns + custom rules from security-rules.json
- `llm_code/runtime/conversation.py` — adaptive thinking 用 provider.supports_reasoning()
- `llm_code/api/provider.py` — 新增 supports_reasoning() 抽象方法
- `llm_code/tools/computer_use_tools.py` — 5 個工具 FULL_ACCESS → WORKSPACE_WRITE
- `llm_code/tools/git_tools.py` — git_push FULL_ACCESS → WORKSPACE_WRITE
- `llm_code/tools/web_fetch.py` — auto-retry with playwright on unrendered JS
- `llm_code/tools/web_search.py` — RateLimitError fallback logging
- `llm_code/tools/search_backends/__init__.py` — 新增 RateLimitError class
- `llm_code/tools/search_backends/duckduckgo.py` — HTTP 429 + bot detection
- `llm_code/marketplace/installer.py` — scan_plugin() + SecurityScanError + audit log
- `llm_code/tools/ide_open.py` — asyncio.new_event_loop() (Python 3.13 fix)
- `llm_code/tools/ide_diagnostics.py` — 同上
- `llm_code/tools/ide_selection.py` — 同上
- `llm_code/__init__.py` — version 1.0.2
- `llm_code/lsp/client.py` — clientInfo version 1.0.2
- `llm_code/mcp/client.py` — clientInfo version 1.0.2
- `pyproject.toml` — version 1.0.2, tiktoken optional dep
- `README.md` — test count 3424, PATH troubleshooting
- `.github/workflows/ci.yml` — Python 3.13 + ARM64 runner

**測試更新：**
- `tests/test_runtime/test_memory.py` — +21 tests
- `tests/test_runtime/test_skills.py` — +7 tests
- `tests/test_runtime/test_session.py` — 更新 estimated_tokens
- `tests/test_runtime/test_compaction.py` — 更新 threshold
- `tests/test_runtime/test_conversation.py` — compact_after_tokens + supports_reasoning mock
- `tests/test_runtime/test_conversation_v2.py` — 同上
- `tests/test_runtime/test_conversation_v4.py` — 同上
- `tests/test_runtime/test_result_budget.py` — compact_after_tokens mock
- `tests/test_runtime/test_thinking_stream.py` — +5 tests (adaptive reasoning + provider interface)
- `tests/test_runtime/test_secret_scanner.py` — +8 tests (custom patterns + cache)
- `tests/test_marketplace/test_installer_integration.py` — +11 tests (scan + audit log)
- `tests/test_tui/test_secret_redaction.py` — +7 tests (new patterns)
- `tests/test_integration.py` — supports_reasoning mock
- `tests/test_integration_v2.py` — supports_reasoning mock
- `tests/test_tools/test_web_search.py` — +4 tests + permission fix
- `tests/test_tools/test_web_fetch.py` — permission fix
- `tests/test_computer_use/test_tools.py` — 5 permission fixes
- `tests/test_tools/test_git_tools.py` — permission fix
- `tests/test_swarm/test_coordinator.py` — permission fix
- `tests/test_swarm/test_tools.py` — 2 permission fixes
- `tests/test_tools/test_agent.py` — permission fix
- `tests/test_tools/test_cron_tools.py` — 2 permission fixes

## 已完成項目摘要

| # | 項目 | 狀態 |
|---|------|------|
| 1 | Skill 適配 — auto skills prompt + command skill tests | ✅ |
| 2 | 命名一致性 — 不加 alias | ✅ |
| 3 | Thinking 模式 — supports_reasoning() + adaptive auto-detect | ✅ |
| 4 | Web fetch — readability + auto-retry + DDG rate limit fallback | ✅ |
| 5a | 安全掃描 — scan_plugin + SecurityScanError + 8 tests | ✅ |
| 5b | Security audit log — `~/.llmcode/security-audit.jsonl` + 3 tests | ✅ |
| 5c | Secret scanner 擴展 — +6 patterns + 7 tests | ✅ |
| 5d | 自訂安全規則 — security-rules.json + load_custom_patterns + 8 tests | ✅ |
| 6 | Token 估算 — +4000 overhead + tiktoken optional | ✅ |
| 7 | 記憶系統測試 — 21 tests (find_related/find_by_tag/episodes) | ✅ |
| 8 | Version bump 1.0.2 — pyproject, __init__, LSP/MCP, README | ✅ |
| 9 | Tool 權限 — computer_use/git_push/stale tests fixed | ✅ |
| 10 | Conversation mock tests — compact_after_tokens + supports_reasoning | ✅ |
| 11 | IDE event loop — 3 files 改 asyncio.new_event_loop() | ✅ |
| 12 | PATH troubleshooting — README 加引導 | ✅ |
| 13 | GitHub Actions — Python 3.13 + ARM64 runner | ✅ |

## 待發佈

所有 TODO 項目已完成。準備好時可以 commit + tag v1.0.2 + publish to PyPI。
