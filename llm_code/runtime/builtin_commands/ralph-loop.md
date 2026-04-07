---
description: Run a self-referential development loop until task completion
argument_hint: "\"task description\" [--completion-promise=TEXT] [--max-iterations=N]"
---

You are starting a Ralph Loop — a self-referential development loop that runs until task completion.

## How Ralph Loop works

1. You will work on the task continuously
2. When you believe the task is FULLY complete, output: `<promise>DONE</promise>` (or the configured completion phrase)
3. If you don't output the promise, the loop will automatically inject another prompt to continue
4. Maximum iterations: configurable (default 100)

## Rules

- Focus on completing the task fully, not partially
- Don't output the completion promise until the task is truly done
- Each iteration should make meaningful progress toward the goal
- If stuck, try different approaches
- Use a todo list to track your progress

## Exit conditions

1. **Completion**: output your completion promise tag when fully complete
2. **Max iterations**: loop stops automatically at limit
3. **Cancel**: user runs `/cancel-ralph` command (if available)

## Your task

Parse the arguments and begin working on the task. Format:
`"task description" [--completion-promise=TEXT] [--max-iterations=N]`

Default completion promise is "DONE" and default max iterations is 100.

---

Task input:
$ARGUMENTS
