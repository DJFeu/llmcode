# Next Session — Resume v2.0.0 REPL Implementation

**Date saved:** 2026-04-11 (end of brainstorming + plan-writing session)
**Repo state:** `main` at `5aa631ef` (pushed to `origin/main`)
**Active release:** `v1.23.1` (stopgap, live on PyPI)
**Next target:** `v2.0.0` (full REPL rewrite)

---

## 貼這段 prompt 進新 session 即可繼續

```
我要繼續執行 llmcode v2.0.0 REPL mode 的實作。

Context：
- 上個 session 已經完成 brainstorming + 寫完 spec + 寫完 M0-M14 共 15 個
  implementation plan 檔，全部 push 到 origin/main。
- v1.23.1 stopgap 已經 release 到 PyPI，包含 mouse/wheel/history 修復。
- 整個 rewrite 的 spec 在 docs/superpowers/specs/2026-04-11-llm-code-repl-mode-design.md
- 15 個 milestone plans 在 docs/superpowers/plans/2026-04-11-llm-code-repl-m*.md

請先讀這兩個檔案理解 context：
1. docs/superpowers/specs/2026-04-11-llm-code-repl-mode-design.md（架構決策）
2. docs/superpowers/plans/2026-04-11-llm-code-repl-m0-poc.md（M0 PoC gate）

然後直接開始執行 M0 PoC plan 的 Task 0.1 → Task 0.8。M0 是 gate — 驗證
Rich Live + prompt_toolkit Application(full_screen=False) 能在 Warp、
iTerm2、tmux 三個終端裡正常共存。如果 M0 失敗，停下來跟我討論是否
退回 Fallback F1 (Strategy B scroll-print)。

注意：
- Task 0.4/0.5/0.6 需要我親自開三個終端跑 python3 experiments/repl_poc.py
  然後人眼驗收。你不能代勞這步，要等我回報結果。
- M0 通過後才能開始 M1（Protocol base）。每個 milestone 的 plan 檔裡
  都有明確的完成條件跟下一個 milestone 的路徑。
- 採用 superpowers:subagent-driven-development 的策略：每個 milestone 
  dispatch 一個 fresh subagent，兩階段 review。這讓 context 不會爆。

現在：先讀 spec 跟 M0 plan，確認你理解全貌，然後列出 Task 0.1 的具體
步驟給我看，等我說 "go" 再動手。
```

---

## 如果你想跳過 M0 直接開始（不建議）

M0 PoC 是 spec §10.1 的 R1/R2 風險的 hard gate。**跳過 M0 = 賭架構是對的**。
如果賭錯，M3 或 M6 會爆，那時候累積的 code 遠比 M0 難回滾。

但如果你堅持跳過，改成：

```
從 M1 Protocol base 開始。docs/superpowers/plans/2026-04-11-llm-code-repl-m1-protocol.md
有 8 個 task（Task 1.0 建 feat/repl-mode branch → Task 1.7 驗證）。
直接執行 Task 1.0 → Task 1.7。
```

---

## 重要檔案速查表

| 想看什麼 | 路徑 |
|---|---|
| 整體設計 | `docs/superpowers/specs/2026-04-11-llm-code-repl-mode-design.md` |
| 所有 plan 的 index | `docs/superpowers/plans/2026-04-11-llm-code-repl-m*.md` 按 M0-M14 順序 |
| v1.23.1 修了什麼 | `CHANGELOG.md` 最頂 |
| Brainstorming 關鍵決策 | spec 第 11 節 "Open Questions (Resolved)" |
| Fallback 退路 | spec 第 10.3 節 |
| hermes-agent 參考 | spec 第 3 節 |

## Milestone 順序與依賴

```
M0 (PoC gate, 1.5h) ──> M1 (Protocol, 2.5h) ──> M2 (REPLPilot, 2h)
                                                  │
                                                  ▼
                                                 M3 (Coordinator, 3h)
                                                  │
              ┌──────────┬─────────┬──────────┬──┴───────┬──────────┐
              ▼          ▼         ▼          ▼          ▼          ▼
             M4         M5        M6         M7         M8         M9
            Input     Status    LiveResp   ToolEvent   Dialog    Voice
            4h        2h        2.5h       3h          3.5h      4h
              │          │         │          │          │          │
              └──────────┴─────────┴──────────┴──────────┴──────────┘
                                    │ (all in parallel)
                                    ▼
                        M10 Dispatcher relocation (8-12h, largest)
                                    │
                                    ▼
                        M11 Cutover + tui/ deletion (2h, flag day)
                                    │
               ┌────────────────────┼────────────────────┐
               ▼                    ▼                    ▼
              M12                  M13                  M14
            Smoke tests        Snapshots (2.5h)      Release (3h)
              3h
```

**Total effort:** ~45-50 hours across all milestones.
**Critical path:** M0 → M1 → M2 → M3 → M10 → M11 → M14 (~25 hours).
**Parallelizable:** M4-M9 (20+ hours saved if run concurrently).

## 上 session 遺漏可檢查的地方

- `experiments/` 目錄還不存在，M0 會建立
- `llm_code/view/` 目錄還不存在，M1 會建立
- `feat/repl-mode` branch 還不存在，M1 的 Task 1.0 會建立
- `tests/test_view/` 還不存在，M1/M2 會建立
- 目前 `tests/test_tui/` 仍然跑 657 個 Textual 測試，M11 才會刪

## 萬一需要 rollback

從 v1.23.1 繼續：`git reset --hard v1.23.1` （會丟失 spec + plan commit）
只保留 spec + plan：目前就是，不用做任何事
把 spec/plan 也刪掉：`git reset --hard bf72f970`

但**建議不要 rollback** — spec + plan 即使暫時不執行也不影響 v1.23.1 的正常使用。
