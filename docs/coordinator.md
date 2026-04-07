# Coordinator: Synthesis-First Multi-Agent Orchestration

llmcode's coordinator is a multi-agent orchestration layer that **forces synthesis before delegation**. It's designed to avoid the most common failure mode of agentic systems: spawning workers prematurely and then trying to glue together garbage.

## The problem with naive coordinators

The standard agentic decomposition pattern:

```
user task → LLM splits into N subtasks → spawn N workers in parallel → glue results
```

This fails predictably:

1. **Workers don't share context**. Each gets a fragment of the user's intent. They make incompatible assumptions.
2. **The coordinator can't tell if a split is even sensible**. It hands off and prays.
3. **On local models**, the LLM tends to over-decompose simple tasks. "Add a print statement" becomes "1. Read the file. 2. Locate the function. 3. Add the print. 4. Verify." Each step is a separate worker. Token usage explodes.
4. **No way to recover**. If a worker produces bad output, you can't easily continue the same worker — you spawn a fresh one and lose all accumulated context.

llmcode's coordinator addresses each of these.

---

## The synthesis-first flow

```
user task
  ↓
synthesize     ← coordinator analyzes task FIRST
  ↓
should_delegate?
  ├── no  → coordinator handles it directly, no workers spawned
  └── yes ↓
decompose
  ↓
spawn workers (or resume existing ones)
  ↓
wait for completion
  ↓
aggregate
  ↓
output + [Resumable swarm member IDs: ...]
```

The key insight: **synthesis happens before decomposition**. The coordinator's first action is to ask the LLM "what do I actually know about this task, and what needs investigation?" — and based on that, decide whether delegation is even warranted.

---

## Step 1 — Synthesis

When `Coordinator.orchestrate("task")` is called, the first LLM call is **not** decomposition. It's a synthesis prompt that returns:

```json
{
  "known_facts": ["the project uses Python", "FastAPI is the web framework"],
  "unknowns": ["which auth library to use", "DB schema for sessions"],
  "should_delegate": true,
  "reason": "Multiple unknowns require parallel investigation"
}
```

If `should_delegate: false`, the coordinator returns immediately with the reason. **No workers are spawned**. This alone catches ~30-50% of cases on local models that would otherwise have spawned 3-5 unnecessary workers for trivial requests.

You can disable synthesis if you want the old naive behavior:
```json
{ "swarm": { "synthesis_enabled": false } }
```

---

## Step 2 — Decomposition (only if delegating)

If synthesis says yes, the coordinator asks the LLM to decompose the task into a JSON array:

```json
[
  { "role": "researcher", "task": "Find the auth library candidates" },
  { "role": "coder", "task": "Implement chosen library" },
  { "role": "tester", "task": "Write integration tests" }
]
```

The result is capped at `swarm.max_members` (default 5). Each subtask becomes a swarm member with its own role label.

---

## Step 3 — Spawning or resuming

For each subtask, the coordinator either:
- **Spawns** a fresh swarm member via `SwarmManager.create_member(role, task)`, or
- **Resumes** an existing member if `resume_member_ids` is provided

### Resume mechanism (v1.4.0+)

```python
# First call — fresh decomposition
result = await coordinator.orchestrate("Build login feature")
# Output ends with: [Resumable swarm member IDs: m1, m2, m3]

# Continue same workers in their accumulated context
result2 = await coordinator.orchestrate(
    "Add email validation to the login work",
    resume_member_ids=["m1", "m2", "m3"],
)
```

When resuming:
- No fresh decomposition happens
- Workers retain everything they learned in the first call
- If any resume target is missing (member died), the coordinator falls through to fresh spawn
- No info leaks: each worker's mailbox is still scoped to its ID

This is how you build long-running agentic workflows that span multiple coordinator turns without losing context.

---

## Step 4 — Waiting for completion

The coordinator polls each member's mailbox via `SwarmManager.mailbox.receive_and_clear()`. A worker is considered done when its mailbox contains "DONE", "COMPLETE", or "FINISHED" (case-insensitive).

Defaults:
- `Coordinator.TIMEOUT = 300.0` (5 minutes)
- `Coordinator.POLL_INTERVAL = 5.0`

You can override per-instance:
```python
coordinator.TIMEOUT = 60.0
coordinator.POLL_INTERVAL = 1.0
```

---

## Step 5 — Aggregation

Once all workers complete (or timeout), the coordinator collects their outputs and asks the LLM to synthesize a final summary. The aggregate prompt includes the original task and each member's output, in role-labeled blocks.

The output is appended with:
```
[Resumable swarm member IDs: m1, m2, m3]
```

so the caller knows which workers can be resumed in the next call.

---

## Context overlap detection

`Coordinator.context_overlap(worker_context, next_task)` computes a token-level overlap score (0.0-1.0) between what a worker has accumulated and what the next task needs. This helps decide:

- **High overlap (> threshold)**: Continue the same worker with `resume_member_ids`
- **Low overlap (< threshold)**: Spawn a fresh worker — context overlap not worth the noise

The default threshold is configurable (`config.swarm.continuation_threshold`).

---

## Practical patterns

### Pattern 1: Long task with multiple stages

```python
# Stage 1
r1 = await coord.orchestrate("Research auth library options")
# Returns analysis + [Resumable IDs: m1, m2]

# Stage 2 — same workers continue with deeper context
r2 = await coord.orchestrate(
    "Pick the best library and design the integration",
    resume_member_ids=parse_ids(r1),
)

# Stage 3 — same workers implement
r3 = await coord.orchestrate(
    "Implement the integration following the design",
    resume_member_ids=parse_ids(r2),
)
```

Each stage benefits from the workers' accumulated context. No repeated explanations needed.

### Pattern 2: Coordinator decides not to delegate

```python
result = await coord.orchestrate("What's 2+2?")
# Synthesis: should_delegate=false, reason="trivial arithmetic"
# Output: "[Coordinator] Skipping delegation: trivial arithmetic"
```

The coordinator returns immediately without spawning anything.

### Pattern 3: Force decomposition

If you want naive parallel split without synthesis:

```json
{
  "swarm": {
    "synthesis_enabled": false,
    "max_members": 5
  }
}
```

Now `orchestrate()` skips synthesis and goes straight to decompose → spawn.

---

## Configuration

```json
{
  "swarm": {
    "enabled": true,
    "max_members": 5,
    "backend": "tmux",
    "synthesis_enabled": true,
    "continuation_threshold": 0.6,
    "role_models": {
      "coder": "claude-sonnet-4-6",
      "researcher": "qwen3.5-32b"
    }
  }
}
```

- `synthesis_enabled` (bool, default `true`): Run pre-decomposition synthesis check
- `continuation_threshold` (float, default `0.6`): Overlap score above which to resume vs spawn fresh
- `role_models` (dict): Per-role model overrides

---

## Why this matters

Naive coordinators on local models fail predictably:
- **Over-spawning**: 3-5 workers for a 2-line fix
- **Context loss**: Each worker reinvents what the others already knew
- **No recovery**: Bad output means starting over from scratch

Synthesis-first + resume gives you:
- **Conservatism by default**: Coordinator only delegates when synthesis says it's worth it
- **Continuity**: Long workflows maintain state across orchestrate calls
- **Debuggability**: You can inspect any worker's mailbox to see what it actually knew

This is one of llmcode's biggest differences from opencode (which uses opencode's `task` tool but doesn't have synthesis-first or context-overlap-based resumption).
