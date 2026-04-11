# llm-code experiments

Throwaway prototypes for validating architecture decisions before they
land in production code. These files are NOT imported by `llm_code` and
are not shipped in the wheel.

Each experiment documents its findings inline or in a sibling `.md` file.

## Current experiments

- `repl_poc.py` — M0 proof-of-concept for the v2.0.0 REPL rewrite.
  Validates that Rich `Live` + prompt_toolkit `Application(full_screen=False)`
  can coexist on Warp, iTerm2, and tmux without redraw corruption.
  See `docs/superpowers/plans/2026-04-11-llm-code-repl-m0-poc.md` for the gate.

## Running an experiment

```bash
/Users/adamhong/miniconda3/bin/python3 experiments/<name>.py
```

## Cleanup

Once an experiment has served its purpose and the findings are captured in
a spec or plan, delete the experiment file. Keep only live experiments here.
