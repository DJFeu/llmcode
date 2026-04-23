# MCP transport

Hayhooks advertises a single tool, `llmcode.run_agent`, to any MCP
client.

## Tool schema

```json
{
  "name": "llmcode.run_agent",
  "description": "Run the llmcode coding agent on a user prompt.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "prompt": {"type": "string"},
      "max_steps": {"type": "integer", "default": 20},
      "tools": {"type": "array", "items": {"type": "string"}, "default": []}
    },
    "required": ["prompt"]
  }
}
```

## Claude Desktop

Add the following snippet to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "llmcode": {
      "command": "llmcode",
      "args": ["hayhooks", "serve", "--transport", "stdio"]
    }
  }
}
```

## Custom clients

Use the `mcp` Python SDK to speak to the stdio transport directly:

```python
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

params = StdioServerParameters(
    command="llmcode",
    args=["hayhooks", "serve", "--transport", "stdio"],
)
async with stdio_client(params) as (read, write):
    async with ClientSession(read, write) as session:
        await session.initialize()
        result = await session.call_tool(
            "llmcode.run_agent",
            {"prompt": "summarise the README"},
        )
        print(result.content[0].text)
```

## SSE transport

For HTTP-bound deployments, `--transport sse` exposes the MCP stream at
`/sse`. Combine with a reverse proxy terminating TLS.
