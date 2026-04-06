# Competitive Feature Enhancement Plan (v2 — Reviewed)

> Date: 2026-04-06
> Source: Plandex, Codex CLI, Aider, aichat, simonw/llm
> Status: Draft — reviewed for positioning fit

---

## Design Principles

在評估每個方向前，先確立 llmcode 的定位：

- **核心身份**: Terminal-native local-LLM coding agent
- **目標用戶**: 自架 LLM（vLLM/Ollama）的開發者
- **差異化**: 本地推論 + agent tool use + memory + TUI
- **不應該做的事**: 與 Open WebUI / vLLM 本身重疊的功能

每個方向用三個問題審查：
1. 是否強化核心身份？
2. 有沒有更成熟的替代品讓我們重複造輪子？
3. 對現有用戶的實際痛點有多大幫助？

---

## 1. Diff Preview — 變更預覽（精簡版）

> 參考: Plandex diff sandbox
> 審查結論: **保留，但大幅精簡**

### 自我審查

| 問題 | 判斷 |
|------|------|
| Plandex 的 virtual FS overlay 適合我們嗎？ | **不適合。** Plandex 是 client-server 架構，server 端維護虛擬檔案系統有道理。llmcode 是單機 CLI，加 overlay 會讓每次讀檔都多一層間接，增加 bug surface，且 LLM 連續編輯同一個檔案時 overlay 的一致性很難維護。 |
| per-replacement 審核有必要嗎？ | **過度設計。** 實務上 LLM 一個回合通常改 1-3 個檔案。per-file 審核就夠了，per-replacement 太碎片化反而降低效率。 |
| 現有 checkpoint + undo 差在哪？ | **缺 diff 預覽。** 使用者看不到 LLM 改了什麼就直接生效了。真正的痛點是「看不到」而不是「回不去」。 |

### 修正後方案: Diff Preview Mode

不做 virtual FS overlay，而是在現有 checkpoint 基礎上加 **diff 顯示**：

```
LLM 寫檔前 → checkpoint 自動建立（已有）
LLM 寫檔後 → 顯示 rich diff（新增）
使用者不滿意 → /undo（已有，改為支援多步）
```

**實作內容：**

1. **Tool 執行後 diff 顯示** — `write_file` / `edit_file` 完成後，在 chat 中顯示 rich diff panel（綠/紅高亮）。不攔截寫入，不改執行流程，只是「讓你看到改了什麼」。

2. **`/undo N`** — 擴展現有 `/undo` 支援回退 N 步（`/undo 3` = 回退 3 個 checkpoint）。現有 `CheckpointManager._stack` 已是 list，只需 pop N 次。

3. **`/diff`** — 顯示自上次 checkpoint 以來的所有變更（`git diff cp-sha..HEAD`）。

### 檔案變更
| 操作 | 檔案 |
|------|------|
| 修改 | `llm_code/tui/app.py` — diff panel 渲染、`/diff` 命令 |
| 修改 | `llm_code/runtime/checkpoint.py` — `undo(n)` 多步支援 |
| 修改 | `llm_code/tui/chat_widgets.py` — DiffPanel widget |

### 預估: ~150 行新增，~30 行修改
### 優先級: **HIGH** — 低成本高回報，不改架構

---

## 2. Suggest Mode — 建議確認模式（精簡版）

> 參考: Codex CLI tiered autonomy
> 審查結論: **保留 suggest mode，砍掉 OS sandbox**

### 自我審查

| 問題 | 判斷 |
|------|------|
| 三檔模式有必要嗎？ | **兩檔就夠。** 現有的 plan mode（唯讀）+ 預設模式（自動執行）之間缺一個「中間檔」。加一個 suggest mode 就完整了。不需要發明新的 enum — 直接復用 `PermissionMode.PROMPT`。 |
| OS-level sandbox 值得做嗎？ | **不值得。** macOS seatbelt 需要 `.sbpl` policy 維護，Linux 需要 bwrap，跨平台測試成本極高。llmcode 的用戶通常跑在自己的開發機上，不是不受信任的環境。這是 Codex CLI 的場景（雲端 API key 呼叫，不信任 model output），不是我們的場景（本地 GPU，使用者控制一切）。 |
| 與方向 1 重疊嗎？ | **部分重疊。** suggest mode 的「確認後執行」和 diff preview 的「執行後檢視」是互補的。suggest mode 適合高風險操作（shell 命令），diff preview 適合低風險操作（檔案編輯）。 |

### 修正後方案

不新增 enum，直接把 `PermissionMode.PROMPT` 暴露為使用者可見的 suggest mode：

```
/mode suggest   → 每個 tool call 需確認（映射到 PROMPT）
/mode normal    → 檔案自動寫，shell 需確認（映射到 WORKSPACE_WRITE，預設）
/mode plan      → 唯讀，只顯示計畫（映射到 PLAN，已有）
```

**實作內容：**

1. **`/mode` 命令** — 切換三種模式，顯示當前模式在 status bar
2. **Suggest UI** — PROMPT 模式下，tool call 前顯示 proposed action panel + `[y/n/a]` 快捷鍵
3. **CLI flag** — `llmcode --mode suggest`
4. **不做 OS sandbox**

### 檔案變更
| 操作 | 檔案 |
|------|------|
| 修改 | `llm_code/tui/app.py` — `/mode` 命令、suggest panel |
| 修改 | `llm_code/tui/status_bar.py` — 顯示當前模式 |
| 修改 | `llm_code/cli/tui_main.py` — `--mode` flag |
| 修改 | `llm_code/runtime/conversation.py` — PROMPT 模式 approval flow |

### 預估: ~120 行新增，~40 行修改
### 優先級: **MEDIUM** — 有價值但現有 plan mode 已覆蓋部分需求

---

## 3. Tree-Sitter Repo Map — 多語言符號索引（務實版）

> 參考: Aider tree-sitter + PageRank
> 審查結論: **保留 tree-sitter，砍掉 PageRank**

### 自我審查

| 問題 | 判斷 |
|------|------|
| tree-sitter 值得引入嗎？ | **值得。** 現有 `repo_map.py` 只對 Python 用 `ast`，JS/TS 用 regex。Go、Rust、Java 完全沒有符號提取。tree-sitter 一次解決所有語言，且是業界標準（VS Code / Neovim 都用）。 |
| PageRank 值得做嗎？ | **ROI 不高。** 引入 `networkx`（~10MB）只為跑一個 PageRank，太重。Aider 需要 PageRank 是因為它的 repo map 要自動決定哪些檔案放進 prompt。llmcode 的 harness guide 已經有 `repo_map_guide` 做上下文注入，用簡單的「近期編輯 + 被引用次數」排序就夠用。 |
| tree-sitter-language-pack 太大嗎？ | **需要控制。** 完整 pack ~100MB。改為 optional dependency，只在用戶需要時安裝。沒安裝時 fallback 到現有 regex。 |
| token budgeting 有必要嗎？ | **有。** 現有 `to_compact(max_tokens=2000)` 用 `chars / 4` 估算太粗糙，且沒有根據上下文窗口動態調整。這是值得做的。 |

### 修正後方案

```
Phase 1: tree-sitter 符號提取（取代 regex）
Phase 2: token budgeting（動態調整 map 大小）
不做: PageRank、networkx dependency
```

**實作內容：**

1. **`treesitter_parser.py`** — tree-sitter 符號提取，optional import
   - 支援語言: Python, TypeScript, JavaScript, Go, Rust, Java, C/C++, Ruby
   - 提取: class/struct/interface 定義、function/method 簽名、import
   - 不提取 references（省掉 PageRank 的需求）

2. **修改 `repo_map.py`** — 嘗試 tree-sitter，失敗則 fallback regex
   ```python
   def _parse_file(path: Path, rel: str) -> FileSymbols:
       try:
           from llm_code.runtime.treesitter_parser import parse_with_treesitter
           return parse_with_treesitter(path, rel)
       except ImportError:
           # fallback to existing ast/regex
           ...
   ```

3. **Token budgeting** — 根據 model context window 動態分配
   ```python
   def compute_map_budget(context_window: int, chat_tokens: int) -> int:
       available = context_window - chat_tokens - 4096  # padding
       return min(max(512, available // 8), 4096)
   ```

4. **Disk cache** — `diskcache` 或簡單的 `shelve`，by file mtime

### 檔案變更
| 操作 | 檔案 |
|------|------|
| 新增 | `llm_code/runtime/treesitter_parser.py` |
| 修改 | `llm_code/runtime/repo_map.py` — 委派 + budgeting |
| 修改 | `llm_code/harness/guides.py` — 動態 budget |
| 修改 | `pyproject.toml` — optional dep |

### 依賴
```toml
[project.optional-dependencies]
treesitter = ["tree-sitter>=0.23", "tree-sitter-language-pack>=0.5"]
```

### 預估: ~250 行新增，~40 行修改
### 優先級: **HIGH** — 直接提升 LLM 回答品質，低風險

---

## 4. Shell Assistant + Model Routing — 輕量高頻功能（取代 HTTP Server）

> 原方向: aichat HTTP server + arena
> 審查結論: **砍掉 HTTP server/playground/arena，改為 shell assistant + model routing**

### 砍掉的理由（完整分析）

| 原功能 | 為何不做 |
|--------|---------|
| OpenAI-compatible API | vLLM 已經是 OpenAI-compatible server，再包一層零附加價值 |
| Playground Web UI | Open WebUI (50k+ stars)、text-generation-webui 已成熟，重造輪子 ROI 為負 |
| Arena 模型對比 | lmsys chatbot-arena 已是業界標準；使用者固定用 Qwen3.5-122B，不頻繁換模型 |
| Session Viewer | 唯一值得做的 web 功能，但優先級低於核心 CLI 功能 |

### 替代方案 A: Shell Assistant（一行模式）

使用者不總是需要啟動完整 TUI。快速的 shell 翻譯很實用：

```bash
# 自然語言 → shell 命令
$ llmcode -x "find all Go files with TODO comments"
→ grep -rn "TODO" --include="*.go" .
Execute? [y/n/edit]

# 快速問答（不啟動 TUI）
$ llmcode -q "what does the -Z flag do in xargs?"
→ (直接輸出答案到 stdout)

# Pipe 模式
$ git diff | llmcode -x "summarize these changes"
→ (讀 stdin，輸出摘要)
```

**實作內容：**

1. **`-x` flag (execute)** — 自然語言轉 shell 命令
   - 送一次 LLM 請求（system prompt: "translate to shell command"）
   - 顯示命令 + 確認
   - 確認後用 `subprocess.run` 執行
   - 不啟動 TUI，不載入 session

2. **`-q` flag (quick)** — 快速問答
   - 送一次 LLM 請求，輸出到 stdout
   - 支援 stdin pipe

3. **實作位置** — `llm_code/cli/oneshot.py`，新增 argparse 處理

### 替代方案 B: Model Routing 強化

現有 `model_routing` config 已有概念，但實際使用不夠智能：

```toml
# .llmcode/config.toml — 現有
[model_routing]
planning = "qwen3.5-122b"
coding = "qwen3.5-122b"
compaction = "qwen3-30b"
```

**強化內容：**

1. **自動 compaction routing** — context 壓縮、session 摘要自動用小模型
   - 目前 compaction 設定存在但不一定被所有路徑使用
   - 確保 dream consolidation、knowledge compilation 都走小模型

2. **`/model route`** — 顯示當前各任務使用的模型和 token 消耗比例
   ```
   ❯ /model route
   coding:      qwen3.5-122b  (85% of tokens)
   compaction:  qwen3-30b     (12% of tokens)
   embedding:   bge-m3        (3% of tokens)
   ```

### 檔案變更
| 操作 | 檔案 |
|------|------|
| 新增 | `llm_code/cli/oneshot.py` — `-x` / `-q` 實作 |
| 修改 | `llm_code/cli/tui_main.py` — argparse 新增 flags |
| 修改 | `llm_code/runtime/config.py` — model routing 驗證 |
| 修改 | `llm_code/tui/app.py` — `/model route` 子命令 |

### 預估: ~200 行新增，~30 行修改
### 優先級: **MEDIUM** — shell assistant 是高頻使用場景，model routing 是成本優化

---

## 5. SQLite Conversation DB — 結構化紀錄（精簡版）

> 參考: simonw/llm SQLite logging
> 審查結論: **保留，但精簡 schema 和範圍**

### 自我審查

| 問題 | 判斷 |
|------|------|
| JSON session 有什麼問題？ | **搜尋慢、統計難。** 要搜尋歷史對話得載入所有 JSON 檔案掃描。要統計 token 使用量得遍歷全部 session。超過 100 個 session 後體驗明顯變差。 |
| 需要 simonw/llm 那麼完整的 schema 嗎？ | **不需要。** 他們有 21 次 migration、attachments、fragments、schemas 表。我們只需要 conversations + messages + FTS。過度設計的 schema 是未來的維護負擔。 |
| 是否與現有 JSON session 衝突？ | **不衝突。** SQLite 是新增層，JSON session 保留作為匯出/備份格式。寫入時雙寫（SQLite + JSON），讀取時優先 SQLite。 |
| FTS5 搜尋真的有用嗎？ | **對重度使用者有用。** 「我上週怎麼解那個 bug 的？」— 這個需求需要跨 session 搜尋。輕度使用者不需要，但不影響他們（SQLite 寫入是透明的）。 |

### 修正後方案: 最小可行 schema

```sql
-- 只有 3 張表，不做 migration 系統（直接 CREATE IF NOT EXISTS）
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    name TEXT,
    model TEXT,
    project_path TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT REFERENCES conversations(id),
    role TEXT NOT NULL,
    content TEXT,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    created_at TEXT
);

CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts
USING fts5(content, content=messages, content_rowid=id);
```

**不做的事：**
- ~~Migration 系統~~ — `CREATE IF NOT EXISTS` 就夠了，等真的需要 alter table 時再加
- ~~tool_calls / tool_results 表~~ — 存在 message content 裡就好
- ~~attachments 表~~ — 不需要
- ~~token_usage 獨立表~~ — 直接用 messages 表的欄位聚合

**做的事：**

1. **`conversation_db.py`** — 最小 DB 層
   ```python
   class ConversationDB:
       def log(self, conv_id, role, content, tokens_in, tokens_out): ...
       def search(self, query, limit=20) -> list[dict]: ...
       def cost_summary(self, since_days=7) -> dict: ...
   ```

2. **整合到 conversation runtime** — 每則 message 完成後 `db.log()`

3. **`/search` 升級** — 改用 FTS5（現有的 in-memory 搜尋保留為 fallback）

4. **`/cost --since 7d`** — 用 SQL 聚合 token 使用量

### 檔案變更
| 操作 | 檔案 |
|------|------|
| 新增 | `llm_code/runtime/conversation_db.py` (~150 行) |
| 修改 | `llm_code/runtime/conversation.py` — 整合 db.log() |
| 修改 | `llm_code/tui/app.py` — `/search` 改用 FTS |

### 預估: ~150 行新增，~30 行修改
### 優先級: **MEDIUM** — 有價值但不是阻塞其他功能的前置條件

---

## Revised Roadmap

### 優先級矩陣

```
                    低成本 ←────────→ 高成本
                    │                    │
    高   ┌──────────┼────────────────────┤
    回   │ 1.Diff   │                    │
    報   │ Preview  │ 3.Tree-sitter      │
         │ (~150行) │   (~250行)         │
         ├──────────┼────────────────────┤
    低   │ 2.Mode   │ 4.Shell Assist     │
    回   │ (~120行) │   (~200行)         │
    報   │ 5.SQLite │                    │
         │ (~150行) │                    │
         └──────────┴────────────────────┘
```

### 實作順序

```
v1.1.0 — 立即可做（~400 行，1-2 天）
├── 1. Diff Preview        ← 最低成本最高回報
├── 2. /mode suggest       ← 復用現有 permissions
└── 5. SQLite DB (minimal) ← 3 張表，無 migration

v1.2.0 — 品質提升（~450 行，2-3 天）
├── 3. Tree-sitter parser  ← 多語言符號索引
├── 3. Token budgeting     ← 動態 map 大小
└── 4. Shell Assistant     ← -x / -q oneshot 模式

v1.3.0 — 選做
├── 4. Model Routing 強化  ← /model route 視覺化
└── 5. /cost --since       ← token 消耗統計
```

### 依賴關係（修正後）

```
1. Diff Preview  → 獨立（只用現有 checkpoint）
2. Suggest Mode  → 獨立（復用現有 permissions）
3. Tree-sitter   → 獨立（optional dep）
4. Shell Assist  → 獨立（新 CLI 入口）
5. SQLite DB     → 獨立（新增層，不替換 JSON）

無互相依賴 → 可並行開發
```

### 總預估（修正前 vs 修正後）

| 方向 | 修正前 | 修正後 | 砍掉了什麼 |
|------|--------|--------|-----------|
| 1. Diff | 400+100 行 | **150+30 行** | virtual FS overlay, per-replacement 審核 |
| 2. Autonomy | 550+100 行 | **120+40 行** | OS sandbox, 新 enum |
| 3. Tree-sitter | 500+50 行 | **250+40 行** | PageRank, networkx |
| 4. Server→Shell | 1900+50 行 | **200+30 行** | HTTP server, playground, arena |
| 5. SQLite | 350+80 行 | **150+30 行** | migration 系統, 多餘的表 |
| **Total** | **~4080 行** | **~1040 行** | **減少 75%** |

---

## 被砍掉的功能及理由總表

| 功能 | 理由 | 替代方案 |
|------|------|---------|
| Virtual FS overlay | 架構侵入性太大，每次讀檔多一層 | checkpoint + diff preview |
| Per-replacement 審核 | 過度設計，per-file 就夠 | /undo N 多步回退 |
| OS-level sandbox | 跨平台維護成本高，非目標場景 | 不做；用戶本機開發不需要 |
| PageRank 排序 | networkx 太重，簡單排序夠用 | 近期編輯 + 引用計數 |
| HTTP server | vLLM 已有 OpenAI API | 不做 |
| Playground | Open WebUI 已成熟 | 不做 |
| Arena | lmsys 已是標準 | 不做 |
| Migration 系統 | CREATE IF NOT EXISTS 就夠 | 等需要時再加 |
| 多餘 DB 表 | attachments/fragments/schemas 不需要 | messages.content 存 JSON |
