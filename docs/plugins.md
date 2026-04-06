# Plugins & MCP

## MCP Servers

Add to config:
```json
{
  "mcpServers": {
    "github": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "env": {"GITHUB_TOKEN": "ghp_xxx"}
    }
  }
}
```

MCP tools appear as `mcp__github__create_issue`, etc.

## Plugin Marketplace

```bash
/plugin search github
/plugin install my-plugin
/plugin list
/plugin enable my-plugin
/plugin disable my-plugin
```

Supports: Official MCP registry, Smithery, npm, GitHub, custom URLs.

## Skills

Place SKILL.md files in `~/.llmcode/skills/` or `.llmcode/skills/`:

```markdown
---
name: my-skill
description: What it does
auto: false
trigger: myskill
---
Your prompt here.
```

`auto: true` = always active. `auto: false` = triggered via `/myskill`.
