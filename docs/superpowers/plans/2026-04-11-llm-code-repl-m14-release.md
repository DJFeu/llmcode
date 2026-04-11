# M14 — Documentation + v2.0.0 Release

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans.

**Goal:** Finalize v2.0.0 for public release. Update README (screenshots, feature matrix, install instructions), write the v2.0.0 CHANGELOG entry (with BREAKING CHANGES section), write `docs/migration-v2.md` for users upgrading from v1.23.x, bump the version, merge `feat/repl-mode` into `main`, tag, push, and create the GitHub Release (which triggers the PyPI publish workflow).

**Architecture:** Pure documentation + release ceremony. No code changes to `llm_code/`. The milestone is sequential: every task must succeed before the next; a failed PyPI publish blocks the release as a user-visible event.

**Tech Stack:** Markdown, git, `gh` CLI, `pyproject.toml`, existing release pipeline (`.github/workflows/publish.yml`).

**Spec reference:** §1 motivation (copy into release notes), §7 user-facing behavior (copy into migration guide), §10.4 success criteria.

**Dependencies:** M1–M13 complete. All tests green on `feat/repl-mode`. Manual verification of 5-step user acceptance (mouse copy, keyboard scroll, Ctrl+G voice, slash popover, `/quit` clean exit) in Warp, iTerm2, and macOS Terminal.

---

## File Structure

- Modify: `pyproject.toml` — version bump `1.23.x` → `2.0.0`
- Modify: `CHANGELOG.md` — prepend v2.0.0 entry (~200 lines)
- Modify: `README.md` — update quickstart, feature matrix, screenshots (~100 lines of diff)
- Create: `docs/migration-v2.md` — upgrade guide for v1.23.x users (~250 lines)
- Modify: `docs/architecture.md` — update to reflect view/ + ViewBackend (~50 lines of diff)

---

## Tasks

### Task 14.1: Manual user acceptance verification

**Files:** none (runtime test + findings recorded in commit or memory)

- [ ] **Step 1: Build fresh wheel locally.**

```bash
/Users/adamhong/miniconda3/bin/python3 -m pip install -e . --no-deps
```

- [ ] **Step 2: Run llmcode in Warp.**

```bash
llmcode
```

Go through the 5-point user acceptance:

1. Type `hello world` + Enter — submits, echoes.
2. Mouse drag-select any line of output → Cmd+C → paste into another app → text appears.
3. Scroll wheel up → terminal native scrollback reveals earlier lines. Status line + input area stay fixed at bottom.
4. Press Ctrl+G → voice recording banner appears. Say a word. VAD stops recording. Transcription inserts into input.
5. Type `/quit` + Enter → REPL exits cleanly, returns to shell prompt.

All 5 must pass. If any fails, STOP the release and file a blocking issue.

- [ ] **Step 3: Repeat in iTerm2 + macOS Terminal.**

Same 5 points. Note any environment-specific differences.

- [ ] **Step 4: Repeat under tmux.**

Same 5 points. tmux is allowed to show minor rendering artifacts during window resize but must not crash.

- [ ] **Step 5: Record findings.**

Update `experiments/M0-FINDINGS.md` with a "v2.0.0 release acceptance" section:

```markdown
## v2.0.0 release acceptance (YYYY-MM-DD)

| Terminal | Mouse copy | Scroll | Voice | Slash popover | Clean exit |
|---|---|---|---|---|---|
| Warp | ✅/❌ | ✅/❌ | ✅/❌ | ✅/❌ | ✅/❌ |
| iTerm2 | ... | ... | ... | ... | ... |
| macOS Terminal | ... | ... | ... | ... | ... |
| tmux (in Warp) | ... | ... | ... | ... | ... |
```

Commit this update.

### Task 14.2: Write docs/migration-v2.md

**Files:** Create `docs/migration-v2.md`

- [ ] **Step 1: Write the guide.** Content structure:

```markdown
# Migrating to llmcode v2.0.0

**v2.0.0 replaces the Textual fullscreen TUI with a line-streaming REPL built on prompt_toolkit + Rich.**
This document explains what changed, why, and what you need to do (usually nothing).

## TL;DR

- Install works the same: `pip install -U llmcode-cli`
- `llmcode` command works the same
- All your config, session checkpoints, and prompt history carry over
- Mouse drag-select-copy now works natively (no Option+drag workaround)
- Scroll wheel scrolls your terminal natively (no `/scroll` command needed)
- Terminal Find (Cmd+F) works because llmcode no longer takes over the full screen

## What changed for you

### Things that work better

(list of 8 user-visible improvements)

### Things that changed form

- **Slash command `/scroll` is removed.** Use your terminal's native scrollback instead.
- **Slash commands `/marketplace browse` and `/plugin browse` are replaced by `/marketplace list` and `/plugin list`.**
- **`/settings` as a modal is replaced by `/settings edit` which opens `$EDITOR` on `~/.llmcode/config.toml`.**
- **Quick Open (`/quick-open`) no longer has a preview pane** — instead, picking a file prints its first 20 lines into the conversation.
- **Marketplace browser no longer uses a card grid** — it's now a selectable list; picking a plugin prints its metadata panel.

### Things that stayed the same

- Every other slash command
- Enter submits, Shift+Enter inserts newline
- Ctrl+↑/↓ recalls prompt history
- Ctrl+G / Ctrl+Space toggles voice input
- Vim mode via `/vim`
- External editor via Ctrl+X Ctrl+E
- All 62 tool integrations
- Session save/load, prompt history, config file formats

## If you have scripts or aliases

If you have a shell alias or automation that does things like:

```bash
# v1.x pattern (still works in v2.0)
echo "quick question" | llmcode

# v1.x pattern with flags (still works)
llmcode -q "one-shot question"
llmcode -x "shell command to explain"
```

These continue to work unchanged.

## If you were affected by the v1.x bugs

Specifically:
- Mouse drag-select-copy blocked by the app
- Scroll wheel not working
- Typing `/` showing a dropdown that looked wrong
- Rate-limit warning overlapping other UI
- Voice hotkey mysteriously triggering on scroll

v2.0.0 fixes all of these. These classes of bugs cannot recur because the fullscreen alt-screen TUI is gone.

## Known differences from v1.23.1

- Quick Open preview pane: replaced by post-pick preview in scrollback
- Marketplace visual browser: replaced by list + metadata panel

## If you need to roll back

`pip install llmcode-cli==1.23.1` installs the last v1.x release.
```

- [ ] **Commit** — `git add docs/migration-v2.md && git commit -m "docs: v2.0.0 migration guide"`

### Task 14.3: Update README.md

**Files:** Modify `README.md`

- [ ] **Step 1: Read README.md** to understand current structure.

- [ ] **Step 2: Update the relevant sections:**

1. **Quickstart / screenshot section**: take a new screenshot of the REPL (real terminal, showing status line + input + a streaming response in scrollback). Replace the old Textual TUI screenshot.

2. **Feature matrix**: update the "what's inside" table to list REPL + ViewBackend as the UI architecture. Remove references to Textual.

3. **Install section**: mention v2.0.0's new native terminal integration as a selling point.

4. **Known compatibility**: add a note that v2.0.0 requires `prompt_toolkit>=3.0.47`, and list tested terminals (Warp, iTerm2, macOS Terminal, tmux, Linux xterm).

5. **Test count badge**: update from `5354` to whatever the current total is after M13.

- [ ] **Commit** — `git commit -am "docs(readme): update for v2.0.0 REPL mode"`

### Task 14.4: Write CHANGELOG v2.0.0 entry

**Files:** Modify `CHANGELOG.md`

- [ ] **Step 1: Prepend a v2.0.0 entry** at the top of CHANGELOG.md.

Structure:

```markdown
## v2.0.0 — REPL Mode: native terminal UX, ViewBackend Protocol, Textual TUI removed

This is a major rewrite of llmcode's view layer, delivering on the
"permanently solve the class of bugs that caused v1.17 through v1.23
to re-flip the mouse capture setting four times" promise.

### 🚨 Breaking changes

- **Textual fullscreen TUI removed.** `llmcode` now launches a
  line-streaming REPL built on prompt_toolkit + Rich. All your config,
  sessions, history, and slash commands carry over; the visual
  presentation is different.
- **4 legacy slash commands removed** (62 → 58):
  `/scroll`, `/marketplace browse`, `/plugin browse`, `/settings` (modal).
  Terminal native scrollback, `/marketplace list`, `/plugin list`,
  and `/settings edit` replace them respectively.
- **Quick Open preview pane removed.** Picking a file now prints its
  first 20 lines into the conversation as a preview, which is
  copyable and searchable in scrollback.
- **Marketplace card grid removed.** Plugins are now shown as a
  selectable list; picking one prints its metadata panel.

### ✨ User-visible improvements

- **Native mouse drag-select-copy** works in Warp / iTerm2 / Kitty /
  macOS Terminal / xterm without holding any modifier.
- **Terminal-native scroll wheel** scrolls the shell's scrollback as
  it should. No more `/scroll` command or `Shift+↑↓` workarounds.
- **Terminal Find (Cmd+F)** works because llmcode no longer enters
  alt-screen mode. Search your conversation history natively.
- **Warp AI block recognition** + **iTerm2 split panes** +
  **tmux copy-mode** all work correctly for the first time.
- **OSC8 hyperlinks** click-through in Warp / iTerm2 / WezTerm.
- **No more wheel-triggered command history** — the v1.23.1 regression
  where scrolling the mouse wheel in Warp would recall `/voice` into
  the input buffer is structurally impossible in the new REPL.

### 🏗 Architecture

- **New `llm_code/view/` package** contains the entire view layer:
  `base.py` (ViewBackend ABC), `types.py` (MessageEvent, StatusUpdate,
  handle Protocols), `dialog_types.py`, `dispatcher.py` (58
  view-agnostic commands), and `repl/` (first-party REPL implementation).
- **ViewBackend Protocol** is the extension point for future platform
  backends — v2.1+ will add Telegram, Discord, Slack, and Web backends
  sharing the same Protocol. The design is inspired by
  [Nous Research's hermes-agent](https://github.com/nousresearch/hermes-agent)
  `BasePlatformAdapter` but kept view-scoped.
- **Old `llm_code/tui/` package is deleted** (~5000 lines, 28 files)
  along with `tests/test_tui/` and `tests/test_e2e_tui/` (~657 tests
  replaced or made obsolete by the new view test suite).

### 🧪 Tests

- **~750 new tests** in `tests/test_view/` + `tests/test_e2e_repl/`
  replace the ~657 deleted Textual tests
- Protocol conformance harness in `tests/test_view/test_protocol_conformance.py`
  — every future `ViewBackend` implementation inherits the base test class
- `REPLPilot` fixture for unified REPL component testing
- 20 `pexpect` smoke tests spawning the real binary
- 25 snapshot goldens for visual regression coverage

### 📦 Dependencies

- **Added:** `prompt_toolkit>=3.0.47`
- **Removed:** `textual>=0.x`
- `pexpect>=4.9.0` added to dev dependencies

### Migration

See `docs/migration-v2.md` for a full upgrade guide. TL;DR: `pip install -U llmcode-cli`.

### Thanks

...

---

## v1.23.1 — (existing entry — do not modify)
...
```

- [ ] **Commit** — `git commit -am "docs(changelog): v2.0.0 entry with breaking changes + architecture notes"`

### Task 14.5: Bump version

**Files:** Modify `pyproject.toml`

- [ ] **Step 1: Edit pyproject.toml.**

Find `version = "1.23.1"` and change to `version = "2.0.0"`.

- [ ] **Step 2: Commit** — `git commit -am "chore: bump version to 2.0.0"`

### Task 14.6: Merge feature branch into main

**Files:** none (git only)

- [ ] **Step 1: Final CI sweep on the feature branch.**

```bash
git checkout feat/repl-mode
/Users/adamhong/miniconda3/bin/python3 -m pytest tests/ -q --tb=no
```

Expected: 0 failures, ~5447 tests.

- [ ] **Step 2: Switch to main and fast-forward rebase.**

```bash
git checkout main
git pull origin main
git rebase main feat/repl-mode  # sync feature branch with any last main hotfixes
```

- [ ] **Step 3: Merge with explicit merge commit (preserve branch history).**

```bash
git checkout main
git merge --no-ff feat/repl-mode -m "chore: merge feat/repl-mode — v2.0.0 REPL rewrite"
```

- [ ] **Step 4: Tag v2.0.0.**

```bash
git tag -a v2.0.0 -m "v2.0.0 — REPL mode, ViewBackend Protocol, Textual TUI removed"
```

- [ ] **Step 5: Push.**

```bash
git push origin main
git push origin v2.0.0
```

### Task 14.7: Create GitHub Release (triggers PyPI publish)

**Files:** none (gh CLI)

- [ ] **Step 1: Extract v2.0.0 changelog section to release notes.**

```bash
awk '/^## v2\.0\.0/{flag=1} /^## v1\.23\.1/{flag=0} flag' CHANGELOG.md > /tmp/v2.0.0-notes.md
```

- [ ] **Step 2: Create the release.**

```bash
gh release create v2.0.0 \
  --title "v2.0.0 — REPL Mode: native terminal UX, ViewBackend Protocol" \
  --notes-file /tmp/v2.0.0-notes.md
```

- [ ] **Step 3: Verify the publish workflow fires.**

```bash
gh run list --workflow=publish.yml --limit 1
```

Expected: an `in_progress` run for v2.0.0. Wait until it completes successfully.

- [ ] **Step 4: Verify PyPI has the new version.**

```bash
pip index versions llmcode-cli
```

Expected: `2.0.0` listed.

- [ ] **Step 5: Announce.**

Post a release announcement wherever the project communicates (README, Discord, X, blog) with a one-line summary + link to `docs/migration-v2.md`.

---

## Milestone completion criteria

- ✅ Manual user acceptance verified in Warp, iTerm2, macOS Terminal, tmux
- ✅ `docs/migration-v2.md` exists and is accurate
- ✅ `README.md` updated with v2.0.0 screenshots and feature matrix
- ✅ `CHANGELOG.md` has v2.0.0 entry with breaking changes clearly flagged
- ✅ `pyproject.toml` version is `2.0.0`
- ✅ `feat/repl-mode` merged into `main` with `--no-ff` preserving branch history
- ✅ `v2.0.0` tag pushed to `origin`
- ✅ GitHub Release created, publish.yml workflow succeeded
- ✅ `pip install llmcode-cli==2.0.0` works from clean venv
- ✅ `llmcode /version` prints `2.0.0`

## Estimated effort: ~3 hours

## Post-release

Monitor:
- GitHub issues count in first 7 days (watch for upgrade regressions)
- Any issues requesting "bring back Textual TUI" — if > 3, reassess the decision
- Discord/community channels for user feedback
- Any CI runs that started failing after the merge

Follow-up for v2.0.1 (if needed):
- Fill snapshot gaps discovered in production
- Additional Warp / iTerm2 / tmux edge cases
- Restoring niche removed functionality if user demand warrants

Follow-up for v2.1.0:
- First platform backend (probably Telegram, given hermes-agent overlap)
- Multi-backend registry in `cli/main.py`
- `docs/adding-a-backend.md` promotion from `llm_code/view/ADDING_A_BACKEND.md`

---

## This is the end of the M0–M14 plan tree. Congratulations on making it here.

After M14, v2.0.0 is live. The architecture is in place for v2.1+ to add platform backends without another view-layer rewrite. The class of bugs that drove this entire rewrite — mouse capture, scroll wheel, alt-screen interactions — cannot recur because the architecture that produced them no longer exists.
