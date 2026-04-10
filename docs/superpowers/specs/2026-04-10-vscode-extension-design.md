# VS Code Extension for llmcode — Design Spec

**Date:** 2026-04-10
**Status:** Approved
**Location:** `extensions/vscode/`

---

## Goal

Build a VS Code extension that provides full IDE integration with llmcode:
1. IDE bridge client (passive responder to llmcode TUI)
2. Embedded chat panel (talk to llmcode inside VS Code)
3. Code actions (right-click "Ask llmcode", diagnostics quick fix, inline diff)

## Architecture

```
extensions/vscode/
├── src/
│   ├── extension.ts          # Entry point, activate/deactivate
│   ├── bridge/               # Layer 1: IDE Bridge Client
│   │   ├── client.ts         # WebSocket JSON-RPC client (ws://localhost:9876)
│   │   ├── protocol.ts       # Type definitions for JSON-RPC messages
│   │   └── handlers.ts       # 4 RPC method handlers
│   ├── chat/                 # Layer 2: Chat Panel
│   │   ├── panel.ts          # Webview panel lifecycle
│   │   ├── process.ts        # Spawn/attach llmcode --serve
│   │   └── renderer.ts       # Markdown + streaming render logic
│   ├── actions/              # Layer 3: Code Actions
│   │   ├── ask.ts            # "Ask llmcode" context menu
│   │   ├── inline-diff.ts    # Inline diff preview via vscode.diff
│   │   └── diagnostics.ts    # "Fix with llmcode" quick fix
│   ├── ui/
│   │   └── status-bar.ts     # Connection status indicator
│   └── config.ts             # Settings reader
├── media/                    # Webview static assets (CSS/JS)
├── package.json              # Extension manifest
├── tsconfig.json
└── README.md
```

## Layer 1: IDE Bridge Client

### Connection

- Connect to `ws://localhost:{bridgePort}` (default 9876)
- On open: send `ide/register` with `{name: "vscode", pid, workspace_path}`
- Auto-reconnect with exponential backoff (1s, 2s, 4s, 8s, max 30s)
- Heartbeat ping every 30 seconds; disconnect if no pong within 10s

### RPC Methods (Server → Extension)

| Method | Params | Handler | VS Code API |
|--------|--------|---------|-------------|
| `ide/openFile` | `{path, line?}` | Open file, move cursor to line | `workspace.openTextDocument()` + `window.showTextDocument()` + `revealRange()` |
| `ide/diagnostics` | `{path}` | Return diagnostics array | `languages.getDiagnostics(uri)` |
| `ide/selection` | `{}` | Return current selection | `window.activeTextEditor.selection` + `document.getText(selection)` |
| `ide/showDiff` | `{path, old_text, new_text}` | Show diff editor | `commands.executeCommand('vscode.diff', ...)` |

### Response Format

```typescript
// Success
{ jsonrpc: "2.0", result: { ok: true }, id: requestId }

// Diagnostics
{ jsonrpc: "2.0", result: { diagnostics: [{ line, severity, message, source }] }, id }

// Selection
{ jsonrpc: "2.0", result: { path, start_line, end_line, text }, id }

// Error
{ jsonrpc: "2.0", error: { code: -32600, message: "..." }, id }
```

## Layer 2: Chat Panel

### Process Management

- **Auto-spawn** (default): Run `llmcode --serve --port 0` as child process.
  Read stdout for the actual port number (server prints `Listening on port XXXXX`).
  Kill on deactivation.
- **Manual**: User sets `llmcode.serverUrl` (e.g. `ws://remote-host:8765`),
  extension connects directly without spawning.
- If spawn fails (llmcode not installed), show notification with install instructions.

### WebSocket Protocol (Remote Server)

Connect to `ws://host:port`. Receive streaming JSON events:

| Event | Data | Rendering |
|-------|------|-----------|
| `welcome` | model, workspace, git_branch | Show in panel header |
| `text_delta` | text chunk | Append to current message, render markdown |
| `text_done` | full text | Finalize message block |
| `thinking_start` | — | Open collapsible "Thinking..." section |
| `thinking_stop` | — | Close thinking section |
| `tool_start` | tool_name, input | Show tool badge with spinner |
| `tool_result` | output, error? | Show result under tool badge |
| `tool_progress` | message | Update tool badge text |
| `turn_done` | usage stats | Show token count, enable input |
| `error` | message | Show error banner |

Send user messages as: `{ type: "user_input", text: "..." }`

### Webview UI

- Sidebar panel (viewContainer in activityBar)
- Message list: user bubbles + assistant bubbles with markdown rendering
- Code blocks with syntax highlighting (use VS Code's built-in tokenizer or highlight.js)
- Thinking sections: collapsible, dimmed text
- Tool calls: collapsible badges showing tool name + result
- Input area at bottom: textarea + send button, Enter to send, Shift+Enter for newline
- `/` commands passed through as-is

## Layer 3: Code Actions

### "Ask llmcode" Context Menu

- Register `editor.action.askLlmcode` command
- Appears in editor context menu when text is selected
- Sends to chat panel: `Regarding {path} lines {start}-{end}:\n\`\`\`\n{selected_text}\n\`\`\`\n`
- Opens chat panel if not visible

### "Fix with llmcode" Quick Fix

- Register `CodeActionProvider` for all languages
- When diagnostics exist on a line, offer "Fix with llmcode" action
- Sends to chat: `Fix this error in {path}:{line}: {diagnostic_message}`

### Inline Diff

- When `ide/showDiff` is received, create two virtual documents (old/new)
- Use `vscode.commands.executeCommand('vscode.diff', oldUri, newUri, title)`
- Diff editor opens as a tab

## Configuration

```jsonc
// package.json contributes.configuration
{
  "llmcode.bridgePort": { "type": "number", "default": 9876 },
  "llmcode.autoConnect": { "type": "boolean", "default": true },
  "llmcode.autoSpawn": { "type": "boolean", "default": true },
  "llmcode.serverUrl": { "type": "string", "default": "" },
  "llmcode.pythonPath": { "type": "string", "default": "" }
}
```

## Commands

| Command | Title | Keybinding |
|---------|-------|------------|
| `llmcode.connect` | Connect to LLMCode | — |
| `llmcode.disconnect` | Disconnect from LLMCode | — |
| `llmcode.openChat` | Open LLMCode Chat | `Ctrl+Shift+L` |
| `llmcode.askAboutSelection` | Ask LLMCode about Selection | — |

## Status Bar

- Left-aligned item: `$(plug) llmcode`
- States: `Connected` (green), `Disconnected` (grey), `Spawning...` (yellow)
- Click action: `llmcode.openChat`

## Dependencies

- `ws` — WebSocket client (Node.js)
- `@vscode/webview-ui-toolkit` — Optional, for consistent VS Code styling in webview
- No other external dependencies

## Server-Side Changes Needed

1. **llmcode --serve --port 0**: Must print actual port to stdout in parseable format
   (e.g. `LLMCODE_PORT=12345`). Check if this already works.
2. **Heartbeat**: Add ping/pong support to `llm_code/ide/server.py` (websockets library
   has built-in ping support, just needs to be enabled).

## Testing Strategy

- **Unit tests**: Mock WebSocket, test each handler independently
- **Integration test**: Spawn real llmcode server, connect extension, verify round-trip
- **Manual test matrix**: VS Code stable + insiders, macOS + Linux

## Estimated Scope

| Component | Lines (est.) |
|-----------|-------------|
| Bridge client + handlers | ~350 |
| Chat panel + process mgmt | ~500 |
| Webview UI (HTML/CSS/JS) | ~400 |
| Code actions | ~200 |
| Status bar + config | ~100 |
| package.json + manifest | ~150 |
| Tests | ~500 |
| **Total** | **~2,200** |
