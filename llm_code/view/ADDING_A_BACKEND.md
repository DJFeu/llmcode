# Adding a new ViewBackend

This doc is for contributors adding a new `ViewBackend` implementation
to llmcode — for example, a Telegram bot frontend, a Discord bot, a
WebSocket-based web UI, or a Slack interactive app.

The existing `REPLBackend` (in `llm_code/view/repl/`) is the reference
implementation. When in doubt, read how REPL does it.

## Prerequisites

- Read `docs/superpowers/specs/2026-04-11-llm-code-repl-mode-design.md`
  sections 4 (Architecture) and 5 (ViewBackend Protocol).
- Understand the `ViewBackend` ABC in `llm_code/view/base.py`.
- Understand the data types in `llm_code/view/types.py` (MessageEvent,
  StatusUpdate, Role, RiskLevel, StreamingMessageHandle, ToolEventHandle).
- Understand the dialog types in `llm_code/view/dialog_types.py`
  (Choice, TextValidator, DialogCancelled).

## Directory layout

Put your backend under `llm_code/view/<platform>/`. For example:

```
llm_code/view/
├── __init__.py
├── base.py
├── types.py
├── dialog_types.py
├── dispatcher.py
├── repl/           # existing reference backend
│   └── ...
└── telegram/       # your new backend
    ├── __init__.py
    ├── backend.py  # class TelegramBackend(ViewBackend)
    ├── renderers.py
    └── ...
```

The `backend.py` module should export a single class `<Platform>Backend`
that inherits from `ViewBackend`.

## Required abstract methods

Every backend must implement these 17 methods (see `view/base.py` for
full signatures):

**Lifecycle** (3): `start`, `stop`, `run`

**Input** (1): `set_input_handler`

**Message output** (2): `render_message`, `start_streaming_message`

**Tool events** (1): `start_tool_event`

**Status** (1): `update_status`

**Dialogs** (4): `show_confirm`, `show_select`, `show_text_input`,
`show_checklist`

**Convenience output** (4): `print_info`, `print_warning`, `print_error`,
`print_panel`

**External editor** (1): `open_external_editor`

## Optional hooks

These have default no-op implementations. Override if your backend
has a sensible reaction:

- `mark_fatal_error(code, message, retryable)`
- `voice_started()` / `voice_progress(seconds, peak)` / `voice_stopped(reason)`
- `clear_screen()`
- `on_turn_start()` / `on_turn_end()`
- `on_session_compaction(removed_tokens)` / `on_session_load(session_id, message_count)`

Bot backends typically don't implement voice UI (the user isn't looking
at a screen) or clear_screen. Web backends implement them all.

## Push-model input handling

Backends are push-model: your `run()` method reads/receives input from
whatever source (PTY for REPL, webhook for Telegram, WebSocket for web)
and calls `await self._input_handler(text)` for each complete submission.
Register the handler at startup via `set_input_handler(callback)` — the
dispatcher does this automatically during llmcode boot.

Don't try to invert this to a pull model; the dispatcher and runtime
assume push semantics universally.

## Streaming messages

`start_streaming_message(role)` returns a `StreamingMessageHandle`
that the dispatcher feeds chunks into:

```python
handle = backend.start_streaming_message(role=Role.ASSISTANT)
for chunk in llm_stream:
    handle.feed(chunk.text)
handle.commit()
```

Your handle implementation decides how to render the in-progress stream.
REPL uses a Rich Live region that refreshes in place, then commits to
scrollback. A Telegram backend might edit a single message over and
over via `editMessageText`, then leave it in final form. A web backend
might push incremental chunks over a WebSocket.

Key invariants:

- `feed()` is callable any number of times before `commit()` or `abort()`.
- `commit()` finalizes and makes the message permanent/visible.
- `abort()` discards the in-progress message (called on Ctrl+C cancel
  or dispatcher error).
- After `commit()`/`abort()`, further `feed()` calls should be no-ops
  (not errors).
- `is_active` is True between start and commit/abort.

## Tool events

`start_tool_event(tool_name, args)` returns a `ToolEventHandle`. The
dispatcher feeds stdout/stderr/diff lines in, then calls `commit_success`
or `commit_failure`.

Style R (REPL's default): inline summary line on start and commit;
automatic expansion for diff-producing tools (edit_file/write_file/
apply_patch) and failures. Bot backends typically render a compact
summary only and link to full output.

## Dialogs

The four `show_*` methods are the user-interaction primitives. REPL
implements them as `prompt_toolkit` Float overlays. Bot backends
typically use inline keyboard components (Telegram, Slack). Web
backends use modal overlays.

Must raise `DialogCancelled` when the user cancels (Esc, back button,
timeout, etc.). Callers catch this and abort the higher-level operation.

## Testing

Your backend must pass the `ViewBackendConformanceSuite` from
`tests/test_view/test_protocol_conformance.py`:

```python
# tests/test_view/test_telegram_backend.py
import pytest
from tests.test_view.test_protocol_conformance import ViewBackendConformanceSuite
from llm_code.view.telegram.backend import TelegramBackend

class TestTelegramBackendConformance(ViewBackendConformanceSuite):
    @pytest.fixture
    async def backend(self, mock_telegram_api):
        b = TelegramBackend(api=mock_telegram_api)
        await b.start()
        yield b
        await b.stop()
```

All the inherited tests should pass without additional work if your
backend respects the Protocol.

Beyond conformance, write backend-specific tests for your
platform-specific quirks (Telegram rate limits, Slack thread handling,
WebSocket reconnect, etc.).

## Registration

Once your backend is done and tests pass, register it in
`llm_code/cli/main.py`:

```python
# v2.0.0: only REPL
backend = REPLBackend(config=config, runtime=runtime)

# v2.1.0+: registry lookup by config
backend_name = config.view_backend  # "repl", "telegram", ...
backend_cls = VIEW_BACKEND_REGISTRY[backend_name]
backend = backend_cls(config=config, runtime=runtime)
```

(The registry itself lands in v2.1.0 along with the first non-REPL
backend; v2.0.0 hardcodes REPL.)

## What NOT to do

- Don't import `prompt_toolkit` or `rich` outside your backend's own
  package. The Protocol is deliberately library-agnostic.
- Don't inspect or mutate `runtime.conversation`, `runtime.cost_tracker`,
  or other runtime state directly. The dispatcher is the only consumer
  of runtime; your backend talks to the dispatcher via the Protocol.
- Don't assume the user has a screen. Telegram users don't see live
  status updates; your `update_status` may be a no-op. That's fine.
- Don't block the asyncio event loop. All I/O must be async-friendly.
  If you need blocking work (e.g., calling a sync SDK), use
  `asyncio.to_thread` or a dedicated executor.
- Don't bypass the dispatcher to call LLM APIs directly. The dispatcher
  owns turn lifecycle, cost tracking, and permission checks. Your
  backend's job is I/O + presentation only.
