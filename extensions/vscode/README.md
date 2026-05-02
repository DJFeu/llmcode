# LLMCode for VS Code

AI coding agent powered by local LLMs, integrated into VS Code.

## Features

- **IDE Bridge** — llmcode TUI can open files, read diagnostics, and get selections from VS Code
- **Chat Panel** — Talk to llmcode directly in the sidebar (auto-spawns or connects to remote server)
- **Formal Server Chat** — Optionally connect the chat panel to `llmcode server start` JSON-RPC sessions
- **Ask llmcode** — Right-click selected code to ask llmcode about it
- **Fix with llmcode** — Quick fix action sends diagnostics to llmcode for resolution

## Setup

1. Install [llmcode](https://github.com/DJFeu/llmcode): `pip install "llmcode-cli[websocket]"`
2. Install this extension
3. The extension auto-connects to the IDE bridge and auto-spawns the chat server

## Settings

| Setting | Default | Description |
|---------|---------|-------------|
| `llmcode.bridgePort` | `9876` | IDE bridge server port |
| `llmcode.autoConnect` | `true` | Auto-connect to bridge on startup |
| `llmcode.autoSpawn` | `true` | Auto-spawn llmcode for chat panel |
| `llmcode.serverUrl` | `""` | Manual debug REPL server URL |
| `llmcode.pythonPath` | `""` | Path to llmcode binary |
| `llmcode.chatProtocol` | `"debug"` | Chat backend: `debug` (`llmcode --serve`) or `formal` (`llmcode server start`) |
| `llmcode.formalServerUrl` | `"ws://127.0.0.1:8080"` | Formal server URL |
| `llmcode.formalServerToken` | `""` | Formal server bearer token; empty falls back to `LLMCODE_SERVER_TOKEN` |
| `llmcode.formalSessionId` | `""` | Existing formal session id; empty creates a new session |
| `llmcode.formalRole` | `"writer"` | Formal session role |

## Formal Server Chat

The default chat protocol is still the legacy debug REPL path. To use the formal
server protocol:

```bash
llmcode server start --host 127.0.0.1 --port 8080
llmcode server token grant "*" --role writer
```

Then set:

```json
{
  "llmcode.chatProtocol": "formal",
  "llmcode.formalServerUrl": "ws://127.0.0.1:8080",
  "llmcode.formalServerToken": "<token from grant>"
}
```

Leave `llmcode.formalSessionId` empty to let VS Code create a new session. Use
an existing session id when connecting with a token scoped to that session.

## Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| Cmd+Shift+L / Ctrl+Shift+L | Open chat panel |

## Development

```bash
cd extensions/vscode
npm install
npm run compile
# Press F5 in VS Code to launch Extension Development Host
```
