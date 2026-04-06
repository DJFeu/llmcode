# TODO — 下一個 Session 繼續

> Created: 2026-04-06
> Context: v1.0.1 完成後的改善項目

## 高優先級

### 1. Qwen + Superpowers Skill 適配
- [ ] Brainstorming skill 不應觸發 agent tool — 應該是對話引導
- [ ] Auto skills 注入 system prompt 後，Qwen 傾向呼叫 agent tool 而不是直接對話
- [ ] 需要在 system prompt 裡指導 model：「auto skills 是對話指引，不需要 spawn agent」
- [ ] 測試其他 command skills（/test-driven-development, /systematic-debugging）

### 2. 命名一致性
- [ ] PyPI package 是 `llmcode-cli`，binary 是 `llmcode`，但舊版安裝了 `llm-code`
- [ ] 使用者可能混淆 `llm-code` vs `llmcode` — 考慮加 alias 或統一
- [ ] `pip install llmcode-cli` 後 `llmcode` 可能不在 PATH

### 3. Thinking 模式完善
- [ ] vLLM 不支援 `--enable-reasoning`（ARM64 DGX Spark 沒有新版 image）
- [ ] 目前用 `enable_thinking: False` 迴避，但犧牲了 model 的推理能力
- [ ] 等 vLLM ARM64 image 更新後重新啟用
- [ ] 或研究在 ARM64 上 build vLLM 的方法

## 中優先級

### 4. Web Search / Fetch 品質
- [ ] web_fetch 拿到 raw JS/CSS 而不是文章內容（新聞網站用 JS render）
- [ ] 考慮用 readability 演算法提取正文（類似 Mozilla Readability）
- [ ] 或整合 Jina Reader API（免費額度）
- [ ] DuckDuckGo 有時 rate limit

### 5. 安全強化 v2
- [ ] Plugin install scanning（計畫中 v1.2.0 的功能）
- [ ] Security audit log（計畫中 v1.2.0 的功能）
- [ ] 參考 ATR/PanGuard 規則格式

### 6. Context Compaction 微調
- [ ] `estimated_tokens()` 嚴重低估（不計 system prompt, tool definitions）
- [ ] 考慮用 tiktoken 或 model-specific tokenizer 做更準確的估算
- [ ] 或完全依賴 API-reported token count（已部分做了）

## 低優先級

### 7. 記憶系統升級
- [x] 跨 session 搜尋（FTS5）— 已完成
- [x] 情節記憶提取（DreamTask）— 已完成
- [x] 雙向連結（Zettelkasten）— 已完成
- [ ] 測試 DreamTask 的 episode 提取是否真正運作
- [ ] 測試 find_related() 和 find_by_tag() 在實際使用中的效果

### 8. PyPI v1.0.2 發佈
- [ ] 包含今天所有後續 fix（skill trigger, permissions, search, memory）
- [ ] 更新 README test count
- [ ] 考慮 GitHub Actions for ARM64 builds

### 9. 其他
- [ ] `computer_use_tools` 全部是 READ_ONLY — screenshot 正確，但 mouse_click/keyboard_type 應該是 WORKSPACE_WRITE
- [ ] git_tools 全部是 READ_ONLY — git_commit, git_push 應該是 WORKSPACE_WRITE
