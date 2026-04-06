# QA Lessons — 2026-04-06 Session Post-Mortem

> 34 commits, 1 session, 從 slash command hint 開始到完成搜尋新聞

## 問題分類

### Category 1: 資料不一致（8 個 fix）
多處維護相同資訊但不同步。

| 問題 | 根因 | 修了什麼 |
|------|------|---------|
| slash commands 缺漏 | 3 份 list 各自維護 | sync KNOWN_COMMANDS / SLASH_COMMANDS / _COMMANDS |
| marketplace 重複項 | OFFICIAL + COMMUNITY 沒去重 | 加 dedup 邏輯 |
| plugin repo 指向錯誤 | 手動維護 repo URL | 對齊 Claude Code 官方架構 |
| 假 community plugins | 虛構了不存在的 plugin | 清理到只有真實存在的 |
| 安裝後 state.json 沒更新 | _install_from_marketplace 沒呼叫 enable() | 加上 |
| 沒 manifest 的 plugin 被忽略 | list_installed 只認 .claude-plugin/plugin.json | 加 fallback |

**系統性改善：** 用 single source of truth 模式。command 列表應該只有一份，其他地方引用它。

### Category 2: 缺少 hot-reload（3 個 fix）
安裝完東西要重啟。

| 問題 | 修了什麼 |
|------|---------|
| skill 安裝要重啟 | 抽出 _reload_skills()，安裝後呼叫 |
| plugin 安裝要重啟 | 同上 |
| MCP 已有 hot-start | 不需要修（唯一做對的）|

**系統性改善：** 所有 install/enable/disable/remove 操作後都應觸發 reload。

### Category 3: Streaming / 渲染 bug（6 個 fix）
TUI 顯示不正確。

| 問題 | 根因 |
|------|------|
| InputBar 高度不縮回 | refresh() 沒帶 layout=True |
| dropdown 只顯示前 12 個 | 固定 [:12] 切片，沒有捲動 |
| cursor 位置錯亂（s/） | cursor=0 在 value="" 之前設定 |
| Cmd+V 只檢查圖片 | on_paste 忽略 event.text |
| `<think>` tag 洩漏 | split delta + 提前 flush |
| stream 結束沒 flush buffer | _raw_text_buffer 殘留 |

**系統性改善：** 需要 TUI integration tests（目前只有 unit tests）。

### Category 4: LLM / Provider 適配（8 個 fix）
vLLM + Qwen 的特定問題。

| 問題 | 根因 |
|------|------|
| DuckDuckGo 0 results | /lite/ endpoint 已壞 |
| web_search/web_fetch 每次問權限 | FULL_ACCESS 而不是 READ_ONLY |
| thinking 內容洩漏為文字 | vLLM 不支援 --enable-reasoning |
| 122.116.x.x 不被認為 local | _is_local 清單太窄 |
| context 49K 超出 32K 限制 | compact_after_tokens=80K，沒有 proactive compaction |
| token 估算不準 | estimated_tokens() 不計 system prompt |
| tool results 累加後爆 | 只在 iteration 開頭壓縮 |
| model 不回答一直搜尋 | max_turn_iterations=10 太多 |

**系統性改善：** 需要 provider compatibility test suite（至少 vLLM + Ollama）。

### Category 5: 安全缺口（已修，但靠人工發現）

| 問題 | 發現方式 |
|------|---------|
| MCP instruction injection | 代碼審查 |
| bash output secret leak | 代碼審查 |
| env variable inheritance | 代碼審查 |

**系統性改善：** 定期 security audit（或整合 ATR/PanGuard）。

---

## 建議的系統性改善

### 1. Single Source of Truth（防止 Category 1）
```python
# commands.py — 唯一的命令定義
COMMANDS: dict[str, CommandDef] = {
    "help": CommandDef(desc="Show help", no_arg=True),
    "search": CommandDef(desc="Search history", no_arg=False),
    ...
}

# input_bar.py, app.py, render.py 都引用 COMMANDS
```

### 2. Integration Test Suite（防止 Category 3）
```python
# tests/test_tui/test_integration.py
async def test_slash_dropdown_opens_and_closes():
    """Dropdown opens on /, closes on Esc, height resets."""

async def test_paste_text_inserts_at_cursor():
    """Cmd+V with text inserts into InputBar."""

async def test_think_tag_hidden_in_streaming():
    """<think>content</think> renders as ThinkingBlock."""
```

### 3. Provider Compatibility Matrix（防止 Category 4）
```
tests/test_provider_compat/
├── test_vllm.py          # vLLM specific (XML fallback, thinking)
├── test_ollama.py        # Ollama specific
├── test_openai.py        # OpenAI API
└── conftest.py           # Skip if provider not available
```

### 4. Automated Smoke Test（防止所有 Category）
```bash
# 每次 release 前跑
llmcode -q "say hi" --model qwen3       # Quick mode works
llmcode -x "echo hello"                  # Shell assistant works
# TUI smoke test via pexpect or similar
```

---

## 今日 Session 統計

| 指標 | 數字 |
|------|------|
| 有效 commits | 34 |
| 新檔案 | ~15 |
| 新增行數 | ~3,000+ |
| 新測試 | 128+ |
| 修復的 bug | 25+ |
| 新功能 | 10+ |
| 發現的問題模式 | 5 類 |
