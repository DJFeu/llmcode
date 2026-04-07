---
description: Start or resume a Sisyphus work session from a plan file
argument_hint: "[plan-name]"
---

You are starting a Sisyphus work session.

## What to do

1. **Find available plans**: search for plan files at `.sisyphus/plans/*.md` (or `.llmcode/plans/*.md` if that directory exists).

2. **Check for active boulder state**: read `.sisyphus/boulder.json` if it exists.

3. **Decision logic**:
   - If `.sisyphus/boulder.json` exists AND the plan is NOT complete (has unchecked checkboxes):
     - Continue work on the existing plan
     - Append the current session to `session_ids`
   - If no active plan OR plan is complete:
     - List available plan files with timestamps and progress
     - If ONE plan: auto-select it
     - If MULTIPLE plans: show the list and ask the user to select

4. **Create/update boulder.json**:

```json
{
  "active_plan": "/absolute/path/to/plan.md",
  "started_at": "ISO_TIMESTAMP",
  "session_ids": ["session_id_1", "session_id_2"],
  "plan_name": "plan-name"
}
```

5. **Read the FULL plan file** before delegating any tasks. Then start executing per the orchestrator workflow.

## Output formats

When listing plans:
```
Available Work Plans

Current Time: <ISO timestamp>

1. <plan-name-1.md> — Modified: <date> — Progress: 3/10 tasks
2. <plan-name-2.md> — Modified: <date> — Progress: 0/5 tasks

Which plan would you like to work on? (Enter number or plan name)
```

When resuming:
```
Resuming Work Session

Active Plan: <plan-name>
Progress: <completed>/<total> tasks
Sessions: <count> (appending current session)

Reading plan and continuing from last incomplete task...
```

When auto-selecting a single plan:
```
Starting Work Session

Plan: <plan-name>
Started: <timestamp>

Reading plan and beginning execution...
```

## Critical

- Always update `boulder.json` BEFORE starting work
- Read the FULL plan file before delegating any tasks
- Follow the orchestrator delegation protocol with full prompts (task, expected outcome, required tools, must do, must not do, context)

User input (optional plan name to select directly):
$ARGUMENTS
