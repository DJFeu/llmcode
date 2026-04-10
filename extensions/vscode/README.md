# LLMCode for VS Code

AI coding agent powered by local LLMs, integrated into VS Code.

## Features

- **IDE Bridge** — llmcode TUI can open files, read diagnostics, and get selections from VS Code
- **Chat Panel** — Talk to llmcode directly in the sidebar (auto-spawns or connects to remote server)
- **Ask llmcode** — Right-click selected code to ask llmcode about it
- **Fix with llmcode** — Quick fix action sends diagnostics to llmcode for resolution

## Setup

1. Install [llmcode](https://github.com/DJFeu/llmcode): `pip install llmcode-cli`
2. Install this extension
3. The extension auto-connects to the IDE bridge and auto-spawns the chat server

## Settings

| Setting | Default | Description |
|---------|---------|-------------|
| `llmcode.bridgePort` | `9876` | IDE bridge server port |
| `llmcode.autoConnect` | `true` | Auto-connect to bridge on startup |
| `llmcode.autoSpawn` | `true` | Auto-spawn llmcode for chat panel |
| `llmcode.serverUrl` | `""` | Manual remote server URL |
| `llmcode.pythonPath` | `""` | Path to llmcode binary |

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
