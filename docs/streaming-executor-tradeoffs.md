# Streaming Tool Executor — Decision Record

**Status**: ARCHIVED (do not implement without revisiting this analysis)
**Date**: 2026-04-08
**Context**: Wave B/C Feature 6 from the Claude Code restored-source survey

## TL;DR

Streaming tool execution (concurrent dispatch of tool calls as they arrive in
the model stream, with semaphore-based parallelism for read-only tools and
exclusive locks for mutating tools) was prototyped as a standalone module and
**deliberately not wired into the conversation turn loop**.

After deep analysis: **ROI is negative** for llm-code's target user (local-LLM
first). Theoretical max benefit is ~5% turn-time reduction on Anthropic API
sessions and **<1% on local Qwen3-122B**. Implementation risk is concentrated
in 5 high-severity coupling points. The flag-only opt-in
(`runtime.use_streaming_tool_executor: bool = False`) was removed during
archival.

## What "streaming tool executor" means

Today (serial execution):

```
1. Model emits full message with N tool_use blocks
2. After StreamMessageStop, iterate tool calls
3. For each call: validate → permission → checkpoint → spawn → wait
                  → emit Start → wait → emit Result → next call
4. Submit all tool_results together for next turn
```

Streaming (proposed):

```
1. Model emits tool_use block 1 → dispatch immediately
2. Model emits tool_use block 2 → dispatch immediately
3. read_file / glob_search / grep_search etc. acquire shared semaphore
   (max_concurrent=4) and run in parallel
4. edit_file / write_file / bash acquire exclusive lock and serialize
5. Results stream back as they complete
6. Aggregate + sort results before submitting to next turn
```

Reference implementation: Claude Code's
`/src/services/tools/StreamingToolExecutor.ts`.

## High-severity risks (🔴)

### 1. Anthropic API tool_result ordering

The Messages API requires `tool_result` blocks in the next turn to appear in
the **same order** as the `tool_use` blocks they reference. Concurrent execution
breaks this naturally — read_file may finish before bash even though bash was
emitted first.

**Mitigation**: aggregate results, sort by tool_use index, then submit.
Doable, but eliminates the "stream as ready" benefit and adds buffering logic.

### 2. Permission interruption mid-stream

```
tool_use 1: read_file  → dispatched, running concurrently
tool_use 2: edit_file  → permission prompt blocks
              ↓
   user thinks for 8 seconds
              ↓
   read_file already finished in background
              ↓
   user denies edit_file
              ↓
   ???
```

Three bad options:

- **Cancel read_file**: discard completed work
- **Submit read_result + denied_error**: model receives mixed state that the
  serial flow would never produce, behavioral inconsistency
- **Drop read_file silently**: violates ordering invariant from #1

There is no clean answer. Each option breaks an existing invariant.

### 3. SpeculativeExecutor collision

`llm_code/runtime/speculative.py` already implements "run read-only tools
in the background during permission prompts so the result is ready when the
user clicks approve". It is its own concurrency mechanism, with its own
overlay cache, hooked into a different point in the turn loop.

Streaming tool executor would either:

- Double-dispatch (both systems try to spawn the same read_file)
- Or replace SpeculativeExecutor entirely, which deletes the "approve = zero
  latency" UX it provides for the existing serial flow

The right way to do streaming would be to **first refactor SpeculativeExecutor
into a shared concurrent-read scheduler**, then build the streaming dispatcher
on top. That's a much larger project than the original feature scope.

### 4. Hook race conditions

Five built-in hooks ship with v1.9.0+: `auto_format`, `auto_lint`,
`intent_classifier`, `context_recovery`, `auto_commit_offer`. Several share
state (file_history, accumulated edit count). Concurrent tool execution lets
two `post_tool_use` hooks fire near-simultaneously against shared state →
race conditions.

**Mitigation**: serial hook dispatch on top of concurrent tool execution
(extra coordinator), or per-hook locks (every hook author has to know to add
one). Complexity bleeds into every hook implementation.

### 5. Error cascade semantics

Serial flow today: tool 2 fails → tool 3 doesn't run → model receives
`[tool_1_ok, tool_2_error]`. Clean.

Concurrent flow: tools 2 and 3 are both already dispatched when 2 fails.
Tool 3 may also fail (because it depended on tool 2's effect that didn't
happen) or succeed in a way that is now meaningless. Model receives
`[tool_1_ok, tool_2_error, tool_3_undefined_state]`.

Cancellation propagation is its own task — not "add a few lines".

## Medium-severity risks (🟡)

### 6. Multi-tool spinner / TUI redesign

`SpinnerLine` shows one phase + one tool name. Concurrent tools need either
multi-row spinners (terminal real-estate cost) or aggregate "running 3 tools"
display (user can't see which is slow). `StreamToolProgress` events for
different tools interleave, breaking the per-tool progress feed.

### 7. Test surface explosion

Current tool tests are linear: per tool kind × {success, error, denied}.
Streaming adds:

- Order permutations (A→B vs B→A completion)
- Partial completion mid-cancellation
- Semaphore exhaustion edge cases
- Exclusive lock contention scenarios
- Race-condition tests for hook state

Realistic estimate: **+200 tests** to maintain equivalent confidence.
Many of these are "test that bad thing did not happen" tests, which are
hard to write and easy to make non-flaky.

### 8. Checkpoint file lock contention

`checkpoint_mgr.create(call.name, validated_args)` writes `checkpoint.json`
per tool call. Concurrent tools racing to write requires file locking or an
in-memory write queue. Small problem but more code to get right.

## Low-severity risks (🟢)

### 9. Debugging is harder for local-LLM users

Serial logs follow chronological causation. Concurrent logs interleave from
multiple tools — a stack trace from tool A appears in the middle of tool B's
output. Local Qwen3-122B is already producing hard-to-debug behavior due to
thinking-mode and slow reasoning; adding interleaved logs makes incident
post-mortems significantly worse.

### 10. Tool widget visual ordering

The `tool_use_id` correlation from commit `86cbd97` keeps the dedup invariant
intact (each tool_id is unique, in-place updates work). But fast tools may
"complete" in their widget before slow tools that were dispatched earlier,
which **looks** like wrong-ordering even though it isn't. Tolerable but
visually off.

## Realistic benefit estimate

Tool-call distribution observed in real llm-code sessions:

| Pattern | Frequency | Time saved by parallelism |
|---|---|---|
| Single tool (read OR edit) | ~50% of turns | 0s |
| 1 read + 1 edit | ~25% | ~0s (read is fast; total time ≈ edit time) |
| Multiple parallel reads | ~10% | 1-3s saved |
| Multiple edits / bash | ~10% | 0s (exclusive lock) |
| Mix concurrent + exclusive | ~5% | 0.5-1s |

**Weighted average: ~0.2s saved per turn.**

Anthropic API user (5-10s/turn): ~4% improvement → barely perceptible
Local Qwen3-122B (30-60s/turn): **<1% improvement → invisible**

## Decision

**Archive the streaming tool executor.** Specifically:

- Delete `llm_code/runtime/streaming_tool_executor.py`
- Delete `tests/test_runtime/test_streaming_tool_executor.py`
- Delete `tests/test_runtime/test_streaming_tool_executor_wiring.py`
- Remove `use_streaming_tool_executor` flag from `RuntimeConfig`
- Keep this document as the decision record

The flag is removed (not just defaulted to False) so future developers don't
discover it and assume it works. The concept lives on in this document.

## When to revisit

This decision should be reconsidered if:

1. **SpeculativeExecutor is refactored** into a shared concurrent-read
   scheduler. Streaming becomes a layer on top, not a replacement.
2. **Anthropic API user share grows substantially** and 4% turn-time
   savings becomes commercially meaningful.
3. **Tool patterns shift toward parallel-friendly** workloads (e.g. heavy
   web browsing with N concurrent fetches). Current real-world distribution
   is dominated by single-tool turns.
4. **Hook system grows a serial-dispatch primitive** so race conditions in
   #4 stop being per-hook concerns.

If any of those happen, start by re-reading risks #1, #2, #3 — they don't
go away even when the benefit grows.

## Alternative paths considered

- **Read-only parallelism only** (no exclusive lock support): simpler, but
  the speculative executor already does this for the only path that matters
  (during permission prompts). Adding a second mechanism is duplication.

- **Background prefetch on speculation** (extend SpeculativeExecutor to
  prefetch the next tool_use the model is likely to emit): high speculative
  cost, hard to bound, and the model may not actually emit what was guessed.
  Rejected.

- **Streaming UI display only, serial execution underneath**: gives the
  *perception* of speed without any of the benefit. Considered dishonest.
  Rejected.

## References

- Original survey: ranked Streaming Tool Executor as Wave B/C Feature 6
- Reference impl: `/src/services/tools/StreamingToolExecutor.ts` (Claude Code restored-src)
- Module that was archived: `llm_code/runtime/streaming_tool_executor.py`
- Related: `llm_code/runtime/speculative.py` (the existing concurrent-read mechanism)
- Related: commit `86cbd97` (tool_use_id correlation, which streaming would need to preserve)
