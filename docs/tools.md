# Tools

## Built-in Tools (20)

### File Operations
| Tool | Permission | Description |
|------|-----------|-------------|
| `read_file` | read_only | Read files with line numbers |
| `write_file` | workspace_write | Create or overwrite files |
| `edit_file` | workspace_write | Search and replace |
| `glob_search` | read_only | Find files by pattern |
| `grep_search` | read_only | Search file contents |
| `bash` | full_access | Execute shell commands |

### Git
| Tool | Permission | Description |
|------|-----------|-------------|
| `git_status` | read_only | Show working tree status |
| `git_diff` | read_only | Show changes |
| `git_log` | read_only | Show commit history |
| `git_commit` | workspace_write | Commit with safety checks |
| `git_push` | full_access | Push to remote |
| `git_stash` | workspace_write | Stash/pop changes |
| `git_branch` | workspace_write | Manage branches |

### Agent
| Tool | Permission | Description |
|------|-----------|-------------|
| `agent` | full_access | Spawn sub-agent (roles: explore, plan, verify) |

### Memory
| Tool | Permission | Description |
|------|-----------|-------------|
| `memory_store` | workspace_write | Save a note |
| `memory_recall` | read_only | Recall a note |
| `memory_list` | read_only | List all notes |

### LSP
| Tool | Permission | Description |
|------|-----------|-------------|
| `lsp_goto_definition` | read_only | Jump to definition |
| `lsp_find_references` | read_only | Find all references |
| `lsp_diagnostics` | read_only | Show type errors/warnings |
