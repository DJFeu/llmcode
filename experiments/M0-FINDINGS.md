# M0 PoC Findings — REPL Mode Architecture Gate

**Date:** 2026-04-12
**PoC script:** `experiments/repl_poc.py`
**Spec:** `docs/superpowers/specs/2026-04-11-llm-code-repl-mode-design.md`
**Plan:** `docs/superpowers/plans/2026-04-11-llm-code-repl-m0-poc.md`

## Environment

- macOS kernel: `Darwin 25.3.0`
- Python: `3.13.9` (`/Users/adamhong/miniconda3/bin/python3`)
- prompt_toolkit: `3.0.52` (spec requires `>=3.0.47`)
- rich: `14.2.0` (spec requires `>=13.7.0`)

## Per-terminal results

### Warp

- Layout correct at launch: YES
- Input echo works: YES
- Streaming Live region renders in place: YES
- Streaming commits to scrollback: YES
- Native scroll wheel works: YES
- Native drag-select copy works: YES
- Resize handled: YES
- Ctrl+D exit clean: YES
- Overall: **PASS**
- Notes: All eight verification steps from Task 0.4 checked by Adam. No quirks reported.

### iTerm2

- Layout correct at launch: YES
- Input echo works: YES
- Streaming Live region renders in place: YES
- Streaming commits to scrollback: YES
- Native scroll wheel works: YES
- Native drag-select copy works: YES
- Resize handled: YES
- Ctrl+D exit clean: YES
- Overall: **PASS**
- Notes: Same checklist as Warp. No observed differences.

### tmux

- Layout correct at launch: YES
- Input echo works: YES
- Streaming Live region renders in place: YES
- Streaming commits to scrollback: YES
- Native scroll wheel works: YES
- Native drag-select copy works: YES
- Resize handled: YES
- Ctrl+D exit clean: YES
- Detach / reattach preserves state: YES
- Overall: **PASS**
- Notes: Historically the most fragile target for `full_screen=False` apps,
  but worked cleanly. Detach / reattach round-trip keeps the status line and
  input area intact. This removes the "tmux users get a reduced fallback"
  caveat from the spec's risk section.

## Gate decision

**PASS** — All three target terminals (Warp, iTerm2, tmux) meet every
criterion in the Task 0.4 checklist, including the two hardest requirements
the spec exists to enable:

1. **Native scroll wheel** works — the PT bottom layout does not capture
   mouse wheel events, so terminal scrollback behaves naturally.
2. **Native drag-select copy** works — mouse_support=False keeps selection
   in the terminal's hands, directly addressing the v1.23.1 pain point that
   motivated the v2.0.0 rewrite.

The two top-tier spec risks are validated:

- **R1** (Rich Live + prompt_toolkit screen contention) — no contention
  observed. The `transient=True` Live region refreshes in place above the
  PT-reserved area and commits to scrollback without overlap.
- **R2** (prompt_toolkit `full_screen=False` bugs at 3.0.47+) — no bugs
  observed at 3.0.52. Resize, Ctrl+D exit, and tmux detach/reattach all work.

**Proceed to M1** (Protocol base — `docs/superpowers/plans/2026-04-11-llm-code-repl-m1-protocol.md`).
Fallback F1 (Strategy B scroll-print) is not needed and can stay on the shelf.

## Recommendation

Ship the PoC findings and advance to M1 immediately. The architecture's
highest-probability failure modes are ruled out at the cost of ~1.5 hours,
so the ~45-hour remaining milestone sequence can proceed with confidence
that the core screen-coordination design is sound. Keep `experiments/repl_poc.py`
in the tree until M3 (ScreenCoordinator) lands, in case we need to replay the
PoC against regressions in the real implementation; delete it as part of the
M11 cutover cleanup.
