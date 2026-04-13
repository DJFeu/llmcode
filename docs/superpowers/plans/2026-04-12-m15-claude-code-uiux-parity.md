# M15 — Claude Code UI/UX Parity Port (Full Scope)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans.

**Goal:** Port the full user-visible UI/UX surface of Claude Code
into llmcode v2.0.0's REPL so the visual + interaction quality
matches. This plan is a **complete inventory** of every Claude
Code component that has a visible analog in v2.0.0 — no items are
deferred to follow-ups. User explicitly required "留 followup 只會
遺忘然後又缺漏沒有補回的時機" and "一週的完整 porting".

## Inventory: how this plan was derived

A full scan of ``../claude-code-sourcemap/restored-src/src/components/``
+ ``hooks/`` + ``utils/`` produced the table below. Every row is
classified as **PORT** (must ship in M15), **ADAPT** (ship as a
simplified Rich/PT equivalent), or **EXCLUDE** (explicit non-goal,
with reason).

| Claude Code surface | Classification | Rationale |
|---|---|---|
| ``LogoV2/*`` (Claude Code mascot logo) | **EXCLUDE** | **Not ported** — llmcode has its own brand; we ship an LLMCODE block-letter gradient logo (tech-blue, Hermes-agent style) instead |
| ``LogoV2/CondensedLogo`` layout pattern | **ADAPT** | Single-line variant of the llmcode logo + model + cwd strip — layout concept only, our own glyphs |
| ``LogoV2/WelcomeV2`` layout pattern | **ADAPT** | Rich Panel inline scrollback, not a fullscreen screen — layout concept only |
| Claude Code per-frame logo animation | **EXCLUDE** | Per-frame animation doesn't suit non-fullscreen PT, and we don't share Claude Code's mascot either way |
| ``Spinner/GlimmerMessage`` | **PORT** | Shimmer color cycling for streaming text |
| ``Spinner/ShimmerChar`` | **PORT** | Per-char color interpolation primitive |
| ``Spinner/SpinnerAnimationRow`` | **ADAPT** | Drive Status Line spinner + streaming tok from same helper |
| ``Spinner/FlashingChar`` | **PORT** | Error-state char flash (tool failure) |
| ``Spinner/TeammateSpinnerLine`` | **PORT** | Sub-agent spinner line (multi-agent presence) |
| ``Spinner/TeammateSpinnerTree`` | **EXCLUDE** | Tree widget needs fullscreen; swarm stays text-based |
| ``PromptInput/PromptInput`` | **ADAPT** | Our M4 InputArea — wire new components into it |
| ``PromptInput/PromptInputFooter`` | **PORT** | Footer hint strip below input |
| ``PromptInput/PromptInputFooterLeftSide`` | **PORT** | Mode indicator (plan/yolo/bash) |
| ``PromptInput/PromptInputFooterSuggestions`` | **ADAPT** | Fold into footer_hint with contextual text |
| ``PromptInput/PromptInputHelpMenu`` | **PORT** | Multi-column slash popover |
| ``PromptInput/ShimmeredInput`` | **ADAPT** | Shimmer on the FINAL committed user echo, not live typing |
| ``PromptInput/inputPaste`` | **PORT** | Text truncation + image paste + placeholder markers |
| ``PromptInput/HistorySearchInput`` | **ADAPT** | Ctrl+R history search as inline completer variant |
| ``PromptInput/PromptInputModeIndicator`` | **PORT** | Same as footer left-side |
| ``PromptInput/VoiceIndicator`` | **ADAPT** | Already in M5 Status Line voice mode; polish with shimmer |
| ``PromptInput/PromptInputQueuedCommands`` | **EXCLUDE** | Queue UI needs fullscreen layout |
| ``ClickableImageRef`` | **PORT** | ``[Image #id]`` markers in the buffer |
| ``ConfigurableShortcutHint`` | **PORT** | Dynamic hint lines based on keybindings |
| ``ContextVisualization`` | **ADAPT** | 5-block fill bar in Status Line |
| ``ContextSuggestions`` | **EXCLUDE** | Too intrusive for inline REPL; users can run /compact themselves |
| ``HelpV2/HelpV2`` | **ADAPT** | Inline categorized Rich Group instead of tab modal |
| ``HelpV2/Commands`` | **ADAPT** | Rich Table per category |
| ``HelpV2/General`` | **PORT** | General intro text at top of /help |
| ``HighlightedCode`` | **PORT** | Rich Syntax with accurate lexer detection |
| ``StructuredDiff`` | **PORT** | Diff with per-line green/red background + line numbers |
| ``StructuredDiffList`` | **PORT** | Multi-file diff aggregation |
| ``FileEditToolDiff`` | **PORT** | Diff rendering specific to edit_file / write_file / apply_patch |
| ``FileEditToolUpdatedMessage`` | **PORT** | "Updated N lines in foo.py" summary message |
| ``FilePathLink`` | **PORT** | OSC8 hyperlinks for file paths in output |
| ``messages/AssistantTextMessage`` | **PORT** | Assistant text uses bright white + bullet prefix |
| ``messages/AssistantThinkingMessage`` | **PORT** | Thinking text with dim accent + toggle |
| ``messages/AssistantToolUseMessage`` | **ADAPT** | Our M7 ToolEventRegion covers this; polish with progress_line |
| ``messages/HighlightedThinkingText`` | **PORT** | Per-char thinking shimmer |
| ``messages/CompactBoundaryMessage`` | **PORT** | Compact boundary divider line |
| ``messages/CompactSummary`` | **PORT** | Summary text rendered after /compact |
| ``messages/HookProgressMessage`` | **PORT** | Hook execution progress display |
| ``messages/UserPromptMessage`` | **PORT** | User prompt echo with ``>`` prefix, bright white |
| ``messages/UserBashInputMessage`` | **PORT** | Bash mode input echo with ``$`` prefix |
| ``messages/UserBashOutputMessage`` | **PORT** | Bash output rendering |
| ``messages/UserImageMessage`` | **PORT** | Image attached placeholder rendering |
| ``messages/UserCommandMessage`` | **PORT** | Slash command echo rendering |
| ``messages/UserTeammateMessage`` | **PORT** | Multi-agent sub-agent response |
| ``messages/UserAgentNotificationMessage`` | **PORT** | Agent notification line |
| ``messages/TaskAssignmentMessage`` | **PORT** | Task assignment echo |
| ``messages/PlanApprovalMessage`` | **PORT** | /plan mode approval line |
| ``messages/RateLimitMessage`` | **PORT** | Rate limit warning (already partially in M5) |
| ``messages/SystemTextMessage`` | **PORT** | System / info text |
| ``messages/AdvisorMessage`` | **PORT** | Advisor hint line |
| ``messages/GroupedToolUseContent`` | **ADAPT** | Our tool events already group naturally in scrollback |
| ``messages/AttachmentMessage`` | **ADAPT** | Pasted attachment markers |
| ``CoordinatorAgentStatus`` | **PORT** | Multi-agent coordinator status block |
| ``AgentProgressLine`` | **PORT** | Single-line agent progress with colored glyph |
| ``agents/AgentsList`` | **EXCLUDE** | Full modal list widget — too heavy |
| ``skills/SkillsMenu`` | **ADAPT** | Use DialogPopover select + metadata panel |
| ``mcp/MCPListPanel`` | **ADAPT** | Use DialogPopover select flow |
| ``mcp/MCPToolListView`` | **ADAPT** | Print Rich Table of MCP tools |
| ``mcp/MCPStdioServerMenu`` | **EXCLUDE** | Full modal navigation — too heavy |
| ``Markdown`` | **PORT** | Rich Markdown with correct heading / list / code styling |
| ``MarkdownTable`` | **PORT** | Rich Table for markdown table blocks |
| ``design-system/Pane`` | **ADAPT** | Rich Panel helper |
| ``design-system/Dialog`` | **ADAPT** | M8 DialogPopover |
| ``design-system/KeyboardShortcutHint`` | **PORT** | Keyboard hint primitive reused across hints |
| ``design-system/StatusIcon`` | **PORT** | Status icon primitive (✓ / ✗ / ⚠) |
| ``design-system/LoadingState`` | **PORT** | Loading state spinner |
| ``design-system/ProgressBar`` | **PORT** | Progress bar primitive |
| ``design-system/Tabs`` | **EXCLUDE** | Tab navigation needs fullscreen |
| ``design-system/FuzzyPicker`` | **EXCLUDE** | Fuzzy fullscreen picker |
| ``design-system/Divider`` | **PORT** | Horizontal rule primitive |
| ``Spinner.tsx`` | **ADAPT** | Already in M5 StatusLine |
| ``StatusLine.tsx`` | **ADAPT** | M5 already has it; enhance |
| ``TokenWarning`` | **PORT** | Inline token warning when context > 80% |
| ``CtrlOToExpand`` | **PORT** | "Ctrl+O to expand" hint after truncated output |
| ``MemoryUsageIndicator`` | **EXCLUDE** | Dev-only feature |
| ``ThinkingToggle`` | **PORT** | "[thinking: N tokens, expand with Ctrl+O]" line |
| ``VimTextInput`` | **ADAPT** | Vim-mode already in M4 keybindings; polish mode indicator |
| ``BashModeProgress`` | **PORT** | Bash mode progress bar during /bash |
| ``buddy/CompanionSprite`` | **EXCLUDE** | User explicitly excluded: "Buddy 系統不要做" |
| ``buddy/useBuddyNotification`` | **EXCLUDE** | Same |
| ``Onboarding`` | **EXCLUDE** | First-run wizard; not in v2.0.0 spec |
| ``AutoUpdater`` / ``AutoUpdaterWrapper`` | **EXCLUDE** | /update covers this |
| ``ConsoleOAuthFlow`` | **EXCLUDE** | Anthropic OAuth brand flow |
| ``Feedback`` | **EXCLUDE** | Brand flow |
| ``Stats`` / ``VirtualMessageList`` / ``MessageSelector`` | **EXCLUDE** | Full modal-screen widgets |

**Total**: 31 PORT + 18 ADAPT + 18 EXCLUDE.

### Two user-mandated constraints on Group A

1. **LLMCODE brand logo — our own identity, not Claude Code's
   mascot.** The welcome screen must show an "LLMCODE" block-
   letter logo in a tech-blue gradient, modeled after the
   Hermes-agent block-letter style (per user reference image:
   block characters with gradient 3D shading). llmcode keeps its
   own visual identity and does not adopt any mascot or logo
   art from Claude Code.

2. **Preserve existing theme color configuration.** Whatever theme
   color settings already exist in ``RuntimeConfig`` (or wherever
   the user's theme preferences live) MUST survive M15. M15 adds
   brand defaults; it does not replace user-configured theme
   colors. If the user has a theme override, the brand palette
   must fall back to honor it.

---

## Non-goals (re-stated)

- **Buddy companion sprite** (explicit user exclusion).
- Claude Code's mascot logo and its per-frame animation — llmcode
  ships its own tech-blue LLMCODE block-letter gradient logo.
- Fullscreen modal screens that require alt-screen mode (Help tab
  modal, MarketplaceBrowser grid, Onboarding wizard, Stats dashboard,
  Feedback survey, LogSelector, MessageSelector, QuickOpenDialog,
  GlobalSearchDialog). These are fundamentally incompatible with
  the native-scrollback trade we made in M0.
- Anthropic brand flows (OAuth, guest passes, channel downgrade,
  desktop handoff, cost threshold, effort callout).
- Dev-only diagnostics (DevBar, DevChannelsDialog, SentryErrorBoundary).

---

## Architecture

Six groups of tasks:

- **A. Welcome + Status + Footer** — logo, panel, shimmer, context
  meter, footer hint, mode indicator
- **B. Input Area UX** — slash popover upgrade, path completer,
  history ghost + bare-arrow recall, paste handler, bash mode
- **C. Message rendering** — bright white assistant text,
  user prompt echo, thinking toggle, Markdown/MarkdownTable,
  compact summary, rate limit message polish
- **D. Tool events** — colored progress line, structured diff,
  syntax-highlighted code blocks, elapsed-time column, file path
  OSC8 hyperlinks
- **E. Multi-agent** — sub-agent response labeling, coordinator
  status, task assignment, plan approval
- **F. Interactive menus + dialogs** — /help categorized table,
  /skill/mcp/plugin marketplace flow, token warning inline,
  compact boundary
- **G. Verify + push**

Cross-cutting modules that multiple groups share:

- ``llm_code/view/repl/style.py`` — brand palette, shimmer math,
  status colors, OSC8 hyperlink writer, icon primitives
- ``llm_code/view/repl/components/design_system.py`` — Rich
  primitives ported from Claude Code's design-system (Divider,
  StatusIcon, KeyboardShortcutHint, LoadingState, ProgressBar)

---

## File inventory

### New files (29)

**Group A (7)**
- ``llm_code/view/repl/style.py``
- ``llm_code/view/repl/components/logo.py``
- ``llm_code/view/repl/components/welcome.py``
- ``llm_code/view/repl/components/shimmer.py``
- ``llm_code/view/repl/components/context_meter.py``
- ``llm_code/view/repl/components/footer_hint.py``
- ``llm_code/view/repl/components/mode_indicator.py``

**Group B (5)**
- ``llm_code/view/repl/components/path_completer.py``
- ``llm_code/view/repl/components/history_ghost.py``
- ``llm_code/view/repl/components/paste_handler.py``
- ``llm_code/view/repl/components/pasted_ref.py``
- ``llm_code/view/repl/components/bash_mode.py``

**Group C (6)**
- ``llm_code/view/repl/components/assistant_text.py`` (bright white + bullet)
- ``llm_code/view/repl/components/user_prompt_echo.py``
- ``llm_code/view/repl/components/thinking_render.py``
- ``llm_code/view/repl/components/markdown_render.py`` (Rich Markdown wrapper with code fence lexer detection)
- ``llm_code/view/repl/components/compact_summary.py``
- ``llm_code/view/repl/components/truncation.py`` (Ctrl+O expand/collapse registry + markers)

**Group D (4)**
- ``llm_code/view/repl/components/progress_line.py``
- ``llm_code/view/repl/components/structured_diff.py``
- ``llm_code/view/repl/components/code_block.py`` (Rich Syntax wrapper used by markdown + diff)
- ``llm_code/view/repl/components/file_link.py`` (OSC8 hyperlinks)

**Group E (3)**
- ``llm_code/view/repl/components/agent_label.py``
- ``llm_code/view/repl/components/coordinator_status.py``
- ``llm_code/view/repl/components/plan_approval.py``

**Group F (4)**
- ``llm_code/view/repl/components/help_table.py``
- ``llm_code/view/repl/components/marketplace.py``
- ``llm_code/view/repl/components/token_warning.py``
- ``llm_code/view/repl/components/design_system.py`` (Divider, StatusIcon, KeyboardShortcutHint, LoadingState, ProgressBar)

### Modified files (14)

- ``llm_code/cli/main.py`` (welcome wiring)
- ``llm_code/view/repl/coordinator.py`` (layout add footer)
- ``llm_code/view/repl/components/input_area.py`` (popover + path + ghost + paste + bash mode wire-up)
- ``llm_code/view/repl/components/slash_popover.py`` (styled display)
- ``llm_code/view/repl/components/status_line.py`` (shimmer + context meter)
- ``llm_code/view/repl/components/live_response_region.py`` (bright white + bullet + markdown wrapper)
- ``llm_code/view/repl/components/tool_event_renderer.py`` (progress line + structured diff)
- ``llm_code/view/repl/components/dialog_popover.py`` (``info_panel_then_confirm`` helper)
- ``llm_code/view/repl/keybindings.py`` (bare Up/Down + Right-accept for completion & ghost + Ctrl+F alias + Ctrl+V paste + Ctrl+R history search + bash mode toggle + Ctrl+O expand/collapse)
- ``llm_code/view/repl/history.py`` (``peek_latest``, ``count_entries``, ``search``)
- ``llm_code/view/stream_renderer.py`` (wire agent label + coordinator status on teammate events)
- ``llm_code/view/dispatcher.py`` (Rich /help + interactive marketplace flows + bash mode handler)
- ``llm_code/runtime/app_state.py`` (``pasted_content`` + ``paste_counter`` + ``bash_mode`` + ``truncation_registry`` fields)
- ``llm_code/view/base.py`` (``print_assistant_text`` Protocol method)

### Tests (new + updated)

**New test files (14)**
- ``tests/test_view/test_logo.py``
- ``tests/test_view/test_welcome.py``
- ``tests/test_view/test_shimmer.py``
- ``tests/test_view/test_context_meter.py``
- ``tests/test_view/test_footer_hint.py``
- ``tests/test_view/test_path_completer.py``
- ``tests/test_view/test_history_ghost.py``
- ``tests/test_view/test_paste_handler.py``
- ``tests/test_view/test_structured_diff.py``
- ``tests/test_view/test_progress_line.py``
- ``tests/test_view/test_agent_label.py``
- ``tests/test_view/test_help_table.py``
- ``tests/test_view/test_no_bare_colors.py`` (grep-gated invariant: every color goes through ``palette.<slot>``)
- ``tests/test_view/test_truncation.py`` (Ctrl+O expand/collapse registry + markers)

**Updated test files**
- ``test_dispatcher.py``, ``test_status_line.py``,
  ``test_stream_renderer.py``, ``test_tool_event_renderer.py``,
  ``test_live_response_region.py``, ``test_input_area.py``,
  ``test_snapshots.py``, ``test_e2e_repl/test_smoke.py``

---

## Tasks

### Group A — Welcome + Status + Footer

#### Task A1 — ``style.py`` (tech-blue brand palette + theme color hooks + shimmer + OSC8)

Brand defaults (llmcode tech-blue, 5-stop gradient used by the
logo shader + as accent/border colors throughout the REPL):

- ``LLMCODE_BLUE_DEEP`` ``#0b2a5e`` (shadow / top-edge)
- ``LLMCODE_BLUE_DARK`` ``#0b4fae`` (logo body low)
- ``LLMCODE_BLUE_MID`` ``#1e7ce8`` (logo body mid)
- ``LLMCODE_BLUE_LIGHT`` ``#4aa8ff`` (logo body high)
- ``LLMCODE_BLUE_HILITE`` ``#b4e1ff`` (top highlight / shimmer peak)
- ``BRAND_ACCENT`` = ``LLMCODE_BLUE_MID`` (border + panel title)
- ``BRAND_MUTED`` = ``LLMCODE_BLUE_DEEP``
- ``ASSISTANT_FG`` = ``bright_white``
- ``USER_FG`` = ``bright_white``
- ``THINKING_FG`` = ``#9ca3af``

**Theme color preservation (non-negotiable).**
``style.py`` must NOT hard-code brand constants at import time —
they are defaults that a user theme can override. Provide a
singleton ``BrandPalette`` dataclass and a
``load_palette(runtime_config) -> BrandPalette`` factory:

- Read ``runtime_config.theme`` (or ``runtime_config.display.theme``,
  whichever exists today — confirm via grep before committing).
- For every brand slot, prefer the user-configured value when
  present, fall back to the M15 default tech-blue tone otherwise.
- Emit a deterministic ``BrandPalette`` instance that every
  component in Group A-F imports via
  ``from llm_code.view.repl.style import palette``.
- The palette is rebuilt once at REPL startup (``cli/main._run_repl``)
  after runtime config is resolved, and stashed on
  ``coordinator._palette`` so later components pick it up.

All downstream helpers (logo, welcome, status line, footer,
message renderers, tool events) read from ``palette.*`` — never
from bare module constants — so a user theme override immediately
propagates. Grep gate: no other M15 file may import a color
constant directly from ``style`` except through ``palette``.

**Semantic color map (text colored by function).** Every visible
text fragment in the REPL must be routed through one of these
named slots — never a bare color literal at the call site.
``palette`` exposes them as attributes so theme overrides
propagate:

| Slot | Default | Used for |
|---|---|---|
| ``assistant_fg`` | ``bright_white`` | streaming + final assistant message body |
| ``assistant_bullet`` | ``LLMCODE_BLUE_MID`` | leading ``●`` on assistant text |
| ``user_fg`` | ``bright_white`` | user prompt echo body |
| ``user_prefix`` | ``LLMCODE_BLUE_LIGHT`` | ``>`` prefix on user echo |
| ``system_fg`` | ``#c7c7c7`` | system / info dispatcher text |
| ``thinking_fg`` | ``#9ca3af`` | thinking block body |
| ``thinking_header_fg`` | ``LLMCODE_BLUE_LIGHT`` | ``[thinking: N tokens]`` header |
| ``tool_name_fg`` | ``bold cyan`` | tool name in start/success/failure line |
| ``tool_args_fg`` | ``dim`` | tool args fragment |
| ``tool_ok_fg`` | ``bold green`` | ``✓`` success glyph + summary |
| ``tool_fail_fg`` | ``bold red`` | ``✗`` failure glyph + error text |
| ``tool_start_fg`` | ``dim cyan`` | ``▶`` start glyph |
| ``tool_elapsed_fg`` | ``dim`` | right-aligned elapsed time |
| ``file_path_fg`` | ``LLMCODE_BLUE_LIGHT`` | any emitted file path (wrapped in OSC8) |
| ``command_fg`` | ``LLMCODE_BLUE_MID`` | slash command name in echo / help |
| ``command_alias_fg`` | ``dim`` | alias shown next to a slash command |
| ``bash_cmd_fg`` | ``bright_green`` | ``$ <cmd>`` echo in bash mode |
| ``bash_out_fg`` | ``default`` | bash stdout body |
| ``bash_err_fg`` | ``red`` | bash stderr body |
| ``diff_add_bg`` / ``diff_add_fg`` | ``#0e4429`` / ``bright_green`` | diff ``+`` lines |
| ``diff_del_bg`` / ``diff_del_fg`` | ``#3a0d0d`` / ``bright_red`` | diff ``-`` lines |
| ``diff_hunk_fg`` | ``cyan`` | ``@@ -a,b +c,d @@`` header |
| ``diff_lineno_fg`` | ``dim`` | gutter line numbers |
| ``token_count_fg`` | ``LLMCODE_BLUE_LIGHT`` | ``N/M tok`` numerals in status line |
| ``agent_palette`` | 6-tone rotating tech-blue/green/magenta/amber/cyan/pink | sub-agent labels |
| ``status_success`` / ``_warning`` / ``_error`` / ``_info`` / ``_dim`` | ``green`` / ``yellow`` / ``red`` / ``LLMCODE_BLUE_MID`` / ``dim`` | generic semantic status glyphs |
| ``markdown_heading`` | ``bold LLMCODE_BLUE_LIGHT`` | markdown H1-H6 in assistant output |
| ``markdown_code_inline`` | ``#e6db74`` | inline `` `code` `` fragments |
| ``markdown_link`` | ``LLMCODE_BLUE_LIGHT underline`` | markdown links |
| ``markdown_quote_fg`` | ``dim italic`` | ``>`` block quotes |
| ``pasted_marker_fg`` | ``dim italic`` | ``[Pasted text #1, N lines]`` / ``[Image #id]`` |
| ``hint_fg`` | ``dim`` | footer hint strip |
| ``mode_plan_fg`` / ``mode_yolo_fg`` / ``mode_bash_fg`` / ``mode_vim_fg`` | ``LLMCODE_BLUE_MID`` / ``yellow`` / ``bright_green`` / ``magenta`` | mode indicator labels |

**Cross-cutting rule.** Every Group A-F task that emits text must
read its color from a named slot above. A grep-gated test
(``test_no_bare_colors.py``) scans ``llm_code/view/repl/`` and
asserts: (a) no module besides ``style.py`` defines a bare color
hex/name literal, (b) every use of Rich ``Text`` / ``Style`` /
``Panel(border_style=...)`` inside ``view/repl/components/`` pulls
from ``palette.<slot>``, (c) every `print_assistant_text` /
``print_info`` / ``print_warning`` / ``print_error`` path uses
its corresponding semantic slot. This guarantees a single theme
override re-tints the entire REPL in one shot.

Shimmer helpers: ``SHIMMER_KEYFRAMES`` (tech-blue gradient ramp),
``shimmer_color(phase)``, ``shimmer_phase_for_time(t)``,
``context_color(pct)``.

OSC8 helpers: ``hyperlink(text, url) -> str`` emits
``\x1b]8;;<url>\x1b\\<text>\x1b]8;;\x1b\\`` (supported in Warp /
iTerm2 / WezTerm).

Icon primitives: ``ICON_SUCCESS = "✓"``, ``ICON_FAILURE = "✗"``,
``ICON_START = "▶"``, ``ICON_WARNING = "⚠"``,
``ICON_INFO = "ℹ"``, ``ICON_BULLET = "●"``, ``ICON_DOT = "·"``.

Tests: (a) default palette returns tech-blue tones, (b) a fake
``runtime_config`` with a theme override round-trips into every
semantic slot, (c) every slot in the semantic color map table
has a non-None default, (d) shimmer phase monotonicity,
(e) context_color grading thresholds, (f) OSC8 envelope,
(g) no component imports a bare color constant (grep assertion
in ``test_no_bare_colors.py``), (h) every message-render helper
used by Groups C-F calls into ``palette.<slot>`` at least once.

**Commit:** ``feat(view): style.py — tech-blue palette with theme override, shimmer, OSC8, icons``

#### Task A2 — LLMCODE block-letter gradient logo (tech-blue, Hermes-agent style)

``llm_code/view/repl/components/logo.py`` renders the 7-letter
word **"LLMCODE"** as ASCII block-letter art with a tech-blue
gradient, emulating the Hermes-agent reference style the user
supplied (block chars + top-to-bottom gradient + subtle 3D
outline edge).

**Glyph grid.** Each letter is 5 rows tall × 5 cols wide, drawn
with Unicode block characters (``█ ▀ ▄ ▌ ▐``). Seven letters plus
inter-letter spacing gives a ~41-col × 5-row banner that fits
inside a Rich Panel on an 80-col terminal:

```text
L L M M M C C C O O O D D E E E   (conceptual layout — actual
                                    glyphs are 5×5 block-char cells)
```

A private ``_GLYPHS: dict[str, list[str]]`` maps each of the 7
letters to its 5-row string template. Glyph drawing uses
``█`` for solid body, ``▀``/``▄`` for top/bottom edges, and one
leading / trailing blank column for kerning.

**Gradient shader.** ``render_llmcode_logo() -> Text`` walks every
cell of the composed glyph buffer and assigns a foreground color
based on the cell's vertical position within the row band, using
the 5-stop tech-blue ramp from ``style.palette``:

| Row | Color stop | Intent |
|---|---|---|
| 0 | ``LLMCODE_BLUE_HILITE`` (``#b4e1ff``) | top highlight |
| 1 | ``LLMCODE_BLUE_LIGHT``  (``#4aa8ff``) | upper body |
| 2 | ``LLMCODE_BLUE_MID``    (``#1e7ce8``) | mid body |
| 3 | ``LLMCODE_BLUE_DARK``   (``#0b4fae``) | lower body |
| 4 | ``LLMCODE_BLUE_DEEP``   (``#0b2a5e``) | shadow / base |

A subtle 1-cell horizontal offset row produces the "3D outline"
feel of the reference image: the body glyph is drawn in the row
color, and the cell immediately below-right gets a ``#061834``
one-shade-deeper drop-shadow tone when the cell above is solid
and its diagonal neighbor is empty (so the shadow only appears
on the outer edge, not inside the letter body). Kept minimal —
no hand-tuned per-letter offsets.

**Theme override path.** The function reads colors via
``palette.llmcode_blue_hilite`` / ``...light`` / ``...mid`` /
``...dark`` / ``...deep`` — NOT from bare module constants — so
a user theme override from Task A1 propagates to the logo
automatically.

**Compact variant.** ``render_llmcode_logo_compact() -> Text``
produces a single-row bold tech-blue "llmcode" (``bold
#1e7ce8``) for places where the full 5-row banner doesn't fit
(e.g. ``/about`` short summary, error banners, condensed
welcome when ``rows < 20``).

**Tests** (``tests/test_view/test_logo.py``):
- Full banner is exactly 5 rows tall
- Every row is the same visual width (monospace alignment)
- Every letter in ``"LLMCODE"`` appears as a distinct glyph
- The 5 gradient stops all appear in the rendered span list
- Theme override swaps all 5 stops when palette is rebuilt
- Compact variant is 1 row and uses the mid tech-blue tone
- Drop-shadow cells use the dedicated shadow tone, never the
  body tone

**Commit:** ``feat(view): LLMCODE block-letter gradient logo (tech-blue, Hermes-agent style)``

#### Task A3 — Welcome panel

``render_welcome_panel(*, version, model, cwd, permission_mode,
thinking_mode)`` returns a Rich ``Panel`` with logo ``Columns`` +
info ``Table.grid`` + hint footer. Wire into ``cli/main._print_welcome``.

**Implementation notes.**
- Import ``render_llmcode_logo`` from ``components.logo`` and
  place it in the Panel body via ``Group(logo, blank, info_table,
  blank, hint_footer)`` so the gradient banner sits above a
  divider + the info grid.
- ``border_style`` = ``palette.brand_accent`` (tech-blue mid) —
  never a hard-coded hex — so a theme override re-tints the
  border automatically.
- ``title`` = ``f"[bold]llmcode v{version}[/bold]"`` centered.
- When terminal height < 20 rows (cold-start on a small split
  pane), fall back to ``render_llmcode_logo_compact()`` and a
  compressed info grid so we don't eat half the viewport.

**Commit:** ``feat(view): welcome panel — LLMCODE logo + info grid + tech-blue brand border``

#### Task A4 — Shimmer streaming text

``shimmer_text(text, frame) -> list[tuple[str, str]]`` returns a
PT ``FormattedText``-compatible per-char styled list. Used by
``StatusLine._render_default`` for the streaming tok count +
spinner glyph. Cached for 100ms via internal timestamp to throttle
redraw cost.

**Commit:** ``feat(view): shimmer helper + streaming status shimmer``

#### Task A5 — Context meter

``render_context_meter(used, limit) -> list[tuple[str, str]]``
returns ``"N/M tok ▁▃▅▇█"`` with the bar colored by
``context_color(pct)``. Replaces the plain ``N/M tok`` fragment
in ``StatusLine._render_default``.

**Commit:** ``feat(view): context fill meter``

#### Task A6 — Footer hint + mode indicator

``FooterHint.render() -> FormattedText`` produces the hint row.
``ModeIndicator.render() -> FormattedText`` produces the right-
side mode label (``[plan]`` / ``[yolo]`` / ``[bash]`` /
``[vim:NORMAL]``). Both wired into ``coordinator._build_layout``
as a 1-row Window below the input. ``ConditionalContainer``
hides the strip while a dialog popover is active.

**Commit:** ``feat(view): footer hint + mode indicator row``

### Group B — Input Area UX

#### Task B1 — Multi-column slash popover + styled display + Right-arrow accept

Swap ``CompletionsMenu`` → ``MultiColumnCompletionsMenu``.
``SlashCompleter`` yields completions with ``display`` as styled
``FormattedText`` (bold name, dim description). Width guard:
fall back to single column if terminal width < 60.

**Accept-completion bindings** (all registered in
``keybindings.py`` under ``_bind_completion_accept(kb)``):

- **Tab** — cycle to next completion (PT default); on 1-match
  menu it accepts.
- **Right arrow (→)** — when a completion menu is visible OR the
  cursor sits at end-of-line with a single-match prefix, accept
  the current (or top) completion. This matches Claude Code's
  input UX and fish-shell convention: Right extends the typed
  prefix to the full matched token. If no completion is pending,
  Right behaves as a normal cursor-right.
- **Enter** — accepts the highlighted completion and immediately
  submits the line (existing PT behavior retained).
- **Esc** — dismisses the popover without accepting.

The Right-arrow binding is a filtered PT key binding guarded by
``has_completions``/``completion_is_selected`` (a local
``Condition``) so it ONLY intercepts Right when there's something
to accept — otherwise normal cursor movement still works across
multi-line buffers.

Tests (``test_input_area.py``):
- Typing ``/h`` + Right accepts ``/help`` and leaves ``/help`` in
  the buffer
- Right with no completion pending moves the cursor by one cell
- Right with multi-match menu visible accepts the highlighted
  entry (not necessarily the first)
- Right inside a multi-line buffer (cursor not at EOL, no menu)
  still moves the cursor one column to the right

**Commit:** ``feat(view): multi-column slash popover with styled meta + Right-arrow accept``

#### Task B2 — Path completer + @file mentions

``PathCompleter`` yields file paths when the current token
starts with ``@`` or ``./`` or ``/``. Merged with
``SlashCompleter`` via ``merge_completers``. OSC8 hyperlinks
on the paths so Warp / iTerm2 users can click through.

**Commit:** ``feat(view): path completer with @file mentions and OSC8 links``

#### Task B3 — History ghost text + bare Up/Down recall + Right-arrow accept

``history.peek_latest(mode)`` returns the most recent entry.
``HistoryGhost`` is a PT ``Processor`` that injects a dim-rendered
preview when the buffer is empty.

**Accept bindings for the ghost:**

- **Tab** — accept the full ghost into the buffer (fish-style).
- **Right arrow (→)** — when the buffer is empty AND a ghost is
  visible, Right accepts the full ghost text (not just one char).
  If the buffer is non-empty or no ghost is pending, Right is a
  normal cursor-right. This is the dual of Task B1's Right-accept
  for the completion menu — same key, same intent (extend to
  full suggestion), disjoint conditions so they never collide.
- **Ctrl+F** — alias for Right-accept (emacs forward-char binding,
  maps to "accept ghost" when a ghost is pending).
- **Esc** — clears the ghost for the current input.

Bare **Up** on row 0 of an empty-or-single-line buffer recalls
history; multi-line cursor movement otherwise. Bare **Down** does
the reverse. Claude Code's row-0 / row-last detection is ported
verbatim.

**Priority with Task B1.** When a completion menu is visible, the
menu's Right-accept (B1) takes precedence. When the menu is NOT
visible and a ghost is pending, ghost's Right-accept (B3) fires.
When neither is active, Right is a normal cursor movement. This
ordering is enforced by per-binding ``Condition`` filters:
``@Condition: has_completions`` for B1, ``@Condition:
buffer_empty_and_ghost_present`` for B3.

Tests:
- Empty buffer + ghost + Right → ghost text is copied into buffer,
  cursor lands at EOL
- Empty buffer + ghost + Tab → same as above (Tab alias)
- Non-empty buffer + Right (no menu, no ghost) → cursor moves one
  column
- Menu visible + Right → menu entry accepted, ghost branch
  skipped (B1 wins)
- Multi-line buffer + Right on non-last-line → cursor moves one
  column, no ghost acceptance

**Commit:** ``feat(view): history ghost text + Up/Down recall + Right-arrow accept``

#### Task B4 — Paste handler (text + image mixed)

``pasted_ref.PastedContent`` dataclass (id, kind, lines, text /
image_bytes). ``paste_handler`` reads clipboard via ``pyperclip``
(text) and ``PIL.ImageGrab`` (image). Long text inserts as
``[Pasted text #id, N lines]``; images insert as ``[Image #id]``.
``AppState.pasted_content`` registry. ``CommandDispatcher.run_turn``
expands markers on submit before calling the renderer.

**Commit:** ``feat(view): paste handler — text truncation + image + mixed content``

#### Task B5 — Bash mode input

``!`` prefix on the input area switches the buffer into "bash mode"
(green prompt border color + ``[bash]`` mode indicator). On submit,
the dispatcher routes the remaining text through the bash tool
instead of through the LLM renderer. Matches Claude Code's
``inputModes.ts`` + ``BashModeProgress`` pattern.

**Commit:** ``feat(view): bash mode input with ! prefix``

### Group C — Message rendering

#### Task C1 — Bright-white assistant text + bullet prefix

``LiveResponseRegion`` commit path renders the final assistant
message with ``style="bright_white"`` and a leading ``● ``
bullet on the first line. User's requested "亮白文字" upgrade.
``view.base.ViewBackend`` gains ``print_assistant_text(text)`` as
a semantic convenience so the dispatcher can emit short system-
level assistant replies without building a live region.

**Commit:** ``feat(view): bright-white assistant text with bullet prefix``

#### Task C2 — User prompt echo

``user_prompt_echo.render(text) -> Text`` produces ``> <text>`` in
bright white. Called by ``CommandDispatcher.run_turn`` right
before delegating to the renderer, so the user's input is echoed
into scrollback cleanly. (Today we rely on the terminal's typed
input line being in the scrollback — that works but loses the
echo once PT erases the input area on submit.)

**Commit:** ``feat(view): echo user prompt into scrollback with > prefix``

#### Task C3 — Thinking text + toggle

``thinking_render.render_thinking(text, elapsed, tokens) -> Group``
returns a dim-colored block with the ``[thinking: N tokens, Xs]``
header and the first N chars of the thinking buffer. Supports a
``collapsed`` variant that just prints the header. Used by
``ViewStreamRenderer._flush_thinking``.

Shimmer on the header via ``HighlightedThinkingText`` port —
thinking header uses a subtle shimmer color to indicate activity.

**Commit:** ``feat(view): thinking text block with dim accent + header shimmer``

#### Task C4 — Rich Markdown wrapper with code fence lexer detection

``markdown_render.render(md: str) -> Markdown`` wraps Rich's
``Markdown`` with:
- Code fences use ``code_block.render_syntax(code, lang)``
- Tables use Rich's built-in ``MarkdownTable`` renderer
- Headings / lists / paragraphs use bright white fg

Used by ``LiveResponseRegion`` final commit path to render the
finished assistant message as properly-formatted Markdown.

**Commit:** ``feat(view): Markdown wrapper with lexer-detected code blocks``

#### Task C5 — Compact summary display

After ``/compact``, print a ``compact_summary.render(before, after,
tokens_saved) -> Panel`` with a divider + count stats. Used by
``dispatcher._cmd_compact``.

**Commit:** ``feat(view): compact summary panel``

#### Task C6 — Ctrl+O expand / collapse toggle for truncated content

Any long block (tool output, thinking text, pasted text, long
assistant message, structured diff) is rendered **truncated by
default** and registers a handle with ``TruncationRegistry`` on
``AppState``. Pressing **Ctrl+O** toggles the most-recently-
registered handle between a collapsed preview and the full body.

**New module** ``llm_code/view/repl/components/truncation.py``:

```python
@dataclass
class TruncatedBlock:
    block_id: int
    kind: Literal["tool_output", "thinking", "pasted_text",
                  "assistant_message", "diff"]
    preview_lines: int          # how many lines the preview shows
    full_body: str              # lazily materialized content
    current_state: Literal["collapsed", "expanded"]
    marker_text: str            # "[… N more lines · Ctrl+O to expand]"

class TruncationRegistry:
    def register(self, kind, full_body, *, preview_lines=10) -> TruncatedBlock
    def toggle_latest(self, console) -> None   # re-emits to scrollback
    def toggle(self, block_id, console) -> None
    def count_truncated(self) -> int
```

**Render contract.** Every renderer that produces potentially
long output calls ``registry.register(kind, full_body)`` first,
then prints the preview followed by the marker line
``[… N more lines · Ctrl+O to expand]`` (via
``palette.hint_fg``). Affected renderers:

- ``progress_line.render_success`` when tool output > 10 lines
- ``thinking_render.render_thinking`` (collapsed variant is the
  default; Ctrl+O prints the full thinking body)
- ``paste_handler`` — ``[Pasted text #id, N lines]`` marker IS
  the truncation marker; Ctrl+O dumps the full pasted text
- ``live_response_region`` when the final assistant message
  exceeds the "long response" threshold (> 80 lines)
- ``structured_diff`` when a diff has > 100 lines — show first
  100, marker, Ctrl+O dumps the rest

**Scrollback re-emission (non-fullscreen trade).** Because we
can't edit scrollback after it's been written, Ctrl+O in
v2.0.0 **appends** the expanded body below the truncation
marker with a ``─── expanded: <kind> #id ───`` divider, and
updates the registry entry's ``current_state`` to ``"expanded"``.
A second Ctrl+O press on the same block prints a
``─── re-collapsed: <kind> #id ───`` divider + the short
preview. This is the pragmatic compromise vs. Claude Code's
fullscreen-redraw model; users see a clear audit trail of
what they expanded.

**Keybinding.** ``keybindings.py`` gains:

```python
@kb.add("c-o", filter=Condition(lambda: registry.count_truncated() > 0))
def _expand_latest(event):
    registry.toggle_latest(coordinator._console)
```

When no truncated blocks are registered, Ctrl+O falls through
(prompt_toolkit's default is no-op on this key; safe).

**AppState wiring.** ``AppState.truncation_registry:
TruncationRegistry`` added in Task A1-adjacent state change.
``ViewStreamRenderer`` and ``ToolEventRegion`` take a registry
reference via constructor so they can register long blocks.

**Tests** (``tests/test_view/test_truncation.py``):
- Register a 200-line block → marker shows "190 more lines"
- ``toggle_latest`` appends full body + expansion divider
- Second toggle prints re-collapse divider + short preview
- Multiple blocks queued in registration order; toggle_latest
  always acts on the most recent UNLESS ``toggle(block_id)``
  is called explicitly
- No truncated blocks → Ctrl+O condition filter blocks the
  binding (no spurious output)
- Thinking kind renders collapsed header + marker by default

**Manual UA step** added to Group G:
- Produce a long bash output (e.g., ``!find /usr -name '*.py'``) →
  observe truncation marker → press Ctrl+O → full output prints
  below the marker with an ``── expanded: tool_output #N ──``
  divider → press Ctrl+O again → re-collapse divider + short
  preview

**Commit:** ``feat(view): Ctrl+O expand/collapse toggle for long content blocks``

### Group D — Tool events

#### Task D1 — Colored progress line

``progress_line.render_start(tool, args) / render_success(tool,
summary, elapsed) / render_failure(tool, error, elapsed, exit_code)``.
Colored glyphs (``▶`` dim cyan / ``✓`` bold green / ``✗`` bold
red), tool name bold, args dim, elapsed right-aligned dim.

**Commit:** ``feat(view): colored tool event progress line``

#### Task D2 — Structured diff (per-line colors + line numbers)

``structured_diff.render(diff_text: str, *, filename=None) -> Group``
parses unified diff and renders:
- Header: ``--- foo.py`` / ``+++ foo.py`` in dim
- Hunks: ``@@ -10,3 +10,5 @@`` in cyan
- Lines: ``- <code>`` with red background, ``+ <code>`` with
  green background, context lines in default
- Left gutter: line numbers for old + new (``10 | 11`` format)
- Syntax highlighting on the code portion via ``code_block``

Replaces the Rich ``Syntax`` call in ``ToolEventRegion._commit_body``
for the auto-expand diff case.

**Commit:** ``feat(view): structured diff with per-line colors + line numbers``

#### Task D3 — Code block with lexer detection

``code_block.render_syntax(code, language=None) -> Syntax`` with
autodetection fallback via ``Syntax.guess_lexer`` when the
language hint is missing. Shared helper used by ``markdown_render``
+ ``structured_diff``.

**Commit:** ``feat(view): code block helper with lexer autodetection``

#### Task D4 — File path OSC8 hyperlinks

Any place we emit a file path (tool event start line, diff
header, error message, compact summary) goes through
``file_link.render_path(path) -> Text`` which wraps the path in an
OSC8 ``file://`` hyperlink. Warp / iTerm2 / WezTerm users can
click through to open.

**Commit:** ``feat(view): OSC8 hyperlinks on file paths``

### Group E — Multi-agent

#### Task E1 — Sub-agent response label

``agent_label.render(agent_name, text) -> Text`` prefixes sub-agent
messages with ``[<agent_name>] `` in the agent's assigned color
(rotating palette). Used when ``ViewStreamRenderer`` detects a
``StreamTextDelta`` from a sub-agent (via ``event.metadata``).

Rotating palette: 6 distinct colors, hashed from agent name so
the same agent always gets the same color within a session.

**Commit:** ``feat(view): sub-agent response label with rotating color palette``

#### Task E2 — Coordinator agent status block

``coordinator_status.render(coordinator_state) -> Panel`` prints
a Panel showing each active sub-agent's status (idle / running /
completed / failed) with their current task summary. Called from
``dispatcher._cmd_swarm`` when ``swarm_manager`` is active.

**Commit:** ``feat(view): coordinator agent status block``

#### Task E3 — Task assignment + plan approval messages

``plan_approval.render(plan_text) -> Panel`` for /plan mode
approval flow. ``task_assignment.render(task_id, title) -> Text``
for task lifecycle echo. Both used by the relevant dispatcher
commands and by the streaming renderer on
``StreamPermissionRequest`` variants.

**Commit:** ``feat(view): plan approval + task assignment message rendering``

### Group F — Interactive menus + dialogs

#### Task F1 — Rich /help categorized output

``help_table.render_help(commands, skills) -> Group`` builds
``Panel(Table)`` entries per category (Core, Mode, Session,
Runtime, Tools, Agents, Input, Command skills). Rewrite
``dispatcher._cmd_help`` to use it.

**Commit:** ``feat(view): Rich categorized /help output``

#### Task F2 — Interactive marketplace flow

``marketplace.list_skills() / list_mcp() / list_plugins()`` return
lists of entries for the ``show_select`` dialog.
``dialog_popover.info_panel_then_confirm(title, body, *, risk)``
helper prints metadata panel to scrollback then awaits a confirm.
Rewrite ``_cmd_skill`` / ``_cmd_mcp`` / ``_cmd_plugin`` no-arg
branches to use the flow. Sub-commands unchanged.

**Commit:** ``feat(dispatcher): interactive /skill /mcp /plugin marketplace flow``

#### Task F3 — Token warning inline

``token_warning.render(used, limit) -> Text`` produces an inline
warning line when context fill > 80%. Triggered by
``ViewStreamRenderer`` on ``StreamMessageStop`` when the
threshold is crossed for the first time in a session.

**Commit:** ``feat(view): inline token warning at 80% context fill``

#### Task F4 — Design system primitives

``design_system.Divider(char, color)``, ``StatusIcon(kind)``,
``KeyboardShortcutHint(keys, action)``, ``LoadingState(text)``,
``ProgressBar(ratio, width)``. Thin wrappers used by group C / D
/ E. Keeps the dependency graph shallow.

**Commit:** ``feat(view): design system primitives — divider/icon/hint/loading/progress``

### Group G — Verification

#### Task G1 — Regenerate snapshots + fix test breakage

Expected goldens affected:
- ``status_line_default``, ``status_line_streaming``,
  ``status_line_rate_limited`` (shimmer + context meter)
- All ``tool_event_*`` (colored progress line + structured diff)
- New goldens for welcome panel, agent label, compact summary

Run ``PYTEST_SNAPSHOT_UPDATE=1 pytest
tests/test_view/test_snapshots.py``, inspect diffs, commit.

**Commit:** ``test(view): regenerate snapshot goldens for M15 UI port``

#### Task G2 — Update pexpect smoke tests

- ``test_cold_start_renders_status_line`` — add context meter assertion
- ``test_slash_popover_shows_top_match`` — verify 2+ completions + description meta
- New: ``test_footer_hint_visible``, ``test_history_ghost_on_empty_buffer``,
  ``test_paste_text_placeholder``
- ``test_all_commands_dispatchable`` remains

**Commit:** ``test(e2e): M15 smoke suite updates``

#### Task G3 — Full green sweep + manual UA + push

- Full ``pytest`` with no failures, no warnings, ruff clean
- Manual UA in Warp + iTerm2 + macOS Terminal + tmux:
  1. Welcome panel with the LLMCODE tech-blue gradient block-
     letter logo + model + cwd + tech-blue brand border (and
     compact-variant fallback on short terminals)
  1a. Set a theme override in ``~/.llmcode/config.*`` → confirm
     the palette, logo gradient, and border all re-tint without
     any M15 default leaking through
  2. Footer hint row visible below input
  3. Status line anchored at bottom with shimmering streaming tok
     + context bar
  4. Typing ``/`` → 3+ rows popover with bold name + dim desc;
     pressing **Right** (→) accepts the highlighted slash command
     and extends the buffer to the full command name
  4a. Typing ``/he`` + Tab → completes to ``/help`` (single match
      shortcut)
  5. Typing ``@`` → file suggestions; **Right** accepts the top
     path completion (fish-style)
  6. Empty buffer → dim ghost text of last command appears;
     pressing **Right** (→) OR **Tab** accepts the full ghost
     into the buffer; pressing **Up** recalls the previous entry
  6a. Non-empty buffer + Right (no menu, no ghost) → cursor still
      moves one column as normal — no ghost/completion hijack
  7. Cmd+V text > 10KB → ``[Pasted text #1, N lines]`` placeholder
  8. Cmd+V image → ``[Image #1]`` marker + queued for next turn
  9. ``!ls`` → bash mode + green border + runs shell command
 10. Assistant response → bright white ``● `` prefix (bullet in
     tech-blue) + Markdown code fences syntax-highlighted;
     inline ``code`` spans use ``markdown_code_inline`` tone,
     headings use ``markdown_heading`` tone, links underlined
 11. Thinking block → dim ``[thinking: N tokens, Xs]`` header +
     ``[… N more lines · Ctrl+O to expand]`` truncation marker;
     press **Ctrl+O** → full thinking body appends below with
     ``── expanded: thinking #1 ──`` divider; press **Ctrl+O**
     again → re-collapse divider + preview
 11a. Long tool output (e.g. ``!find /usr -name '*.py' 2>/dev/null``)
      → preview + truncation marker → Ctrl+O expands → Ctrl+O
      re-collapses. Works for tool_output, pasted_text,
      assistant_message (>80 lines), and diff (>100 lines).
 11b. No truncated blocks pending → Ctrl+O is a no-op (binding
      filter guards it)
 12. Tool event start → ``▶`` dim cyan, success → ``✓`` green
 13. Edit file diff → per-line green/red background + line numbers
 14. ``/help`` → categorized Rich tables
 15. ``/skill`` no-arg → select dialog → metadata panel → confirm
 16. Sub-agent response → ``[agent_name]`` label in rotating color
 17. Context > 80% → inline token warning line
 18. Voice recording auto-stops after 2s silence (M15 is not
     about voice — sanity check only)
 19. **Semantic color round-trip.** Set a theme override in
     ``~/.llmcode/config.*`` that redefines ``assistant_bullet``
     + ``diff_add_fg`` + ``mode_bash_fg``. Confirm: the assistant
     bullet re-tints, the diff-add color re-tints across every
     structured diff shown in this session, and the ``[bash]``
     mode indicator label re-tints — all without touching source.
- Push ``feat/repl-mode``

**Commit:** ``chore: verify M15 — manual UA pass on Warp/iTerm2/Terminal/tmux``

---

## Risk register

### R1 — MultiColumnCompletionsMenu narrow-terminal quirks
Fall back to single column when terminal width < 60.

### R2 — Shimmer cost on slow terminals
Throttle to 100ms recomputation window, cache the last FormattedText.

### R3 — Rich Panel in DialogPopover Float
``info_panel_then_confirm`` prints the panel to scrollback via
``view.print_panel`` (direct Rich Console), THEN awaits
``show_confirm``. No Rich Panel inside a Float.

### R4 — Paste handler optional deps
``pyperclip`` + ``PIL.ImageGrab`` are optional. Fall back to text-
only paste with a warning when PIL is missing. Fall back to
terminal-native paste when pyperclip is missing (both on Linux
without xclip and on bare systems).

### R5 — Snapshot golden churn
Multiple M13 goldens change across Group A, C, D. Regenerate
per-task, commit goldens in the same commit as the code change.

### R6 — Bare Up/Down ambiguity
Bare Up on empty or single-line buffer → history recall.
Multi-line buffer with cursor on row > 0 → cursor up. Row 0 with
multiple lines → history recall AND show the rest of the lines
greyed out (Claude Code's behavior).

### R7 — OSC8 compatibility
OSC8 hyperlinks silently fail on terminals that don't support
them (xterm without the patch, older tmux). They render as the
raw text, no breakage.

### R8 — Structured diff parse failures
Malformed unified diffs (non-unified, binary markers, git rename
chunks) fall through to the existing Rich Syntax path without
per-line backgrounds.

### R9 — Bash mode + voice collision
Ctrl+G while in bash mode still triggers voice — fine, voice
input transcribes into the bash buffer. No semantic conflict.

### R11 — Ctrl+O scrollback model trade-off
Non-fullscreen PT cannot edit already-written scrollback, so
Ctrl+O **appends** the expanded body under a divider instead of
redrawing in-place. This is a deliberate trade documented in
Task C6. Tests verify the append behavior + divider text;
manual UA confirms the audit trail reads cleanly in Warp /
iTerm2 / macOS Terminal / tmux. If future work adds alt-screen
mode, the truncation module can be upgraded to in-place redraw
without changing its public interface (``TruncationRegistry``
is the shared abstraction).

### R10 — History ghost + completion menu both competing for Tab/Right
Priority: completion menu visible > ghost text pending > default
cursor movement. Tab ordering is the same: menu accept > ghost
accept > default tab insert. Enforced via per-binding
``Condition`` filters (``has_completions`` vs.
``buffer_empty_and_ghost_present``). Tests in B1 + B3 cover all
six branches (menu-visible × Tab/Right, ghost-only × Tab/Right,
neither × Tab/Right).

---

## Milestone completion criteria

- ✅ 28 new files under ``llm_code/view/repl/``
- ✅ 14 existing files modified
- ✅ 12 new test files + updates to 8 existing tests
- ✅ Snapshot goldens regenerated and manually reviewed
- ✅ Full project suite: 0 failures, 0 warnings, ruff clean
- ✅ 18-point manual UA passes in Warp (+ spot-check in iTerm2,
  macOS Terminal, tmux)
- ✅ Branch pushed to ``origin/feat/repl-mode``

## Estimated effort

~40 hours across 32 tasks (A1–A6 + B1–B5 + C1–C5 + D1–D4 +
E1–E3 + F1–F4 + G1–G3 = 32). Breakdown:

- Plan + inventory: 2h (this doc)
- Group A: 5h
- Group B: 7h
- Group C: 5h
- Group D: 6h
- Group E: 4h
- Group F: 5h
- Group G: 3h (verify) + 3h (manual UA + snapshot)

Commits: ~32. Expected to fit in ~5 working days of focused
execution — matches the user's "一週的完整 porting" budget.

## Next milestone

None — M15 completes the visible v2.0.0 scope. M14's merge-to-
main + tag + PyPI publish sequence runs after M15 closes.
