---
name: playwright
description: Use when the user needs browser automation, web verification, scraping, screenshots, or end-to-end testing via Playwright. Triggers on phrases like "open this URL", "screenshot", "fill the form", "scrape", "browser test".
auto: false
tags: [browser, testing, automation]
---

# Playwright Browser Automation

Browser automation via the Playwright MCP server. Use this skill when the user
asks to navigate, interact with, verify, or capture content from a website.

## MCP configuration hint

If the Playwright MCP server is not yet configured, add it to `~/.llmcode/mcp.json`:

```json
{
  "mcpServers": {
    "playwright": {
      "command": "npx",
      "args": ["@playwright/mcp@latest"]
    }
  }
}
```

After saving, restart llmcode so the MCP server is loaded.

## Core workflow

1. Navigate to the page (`browser_navigate`)
2. Take a snapshot to discover interactive elements (`browser_snapshot`)
3. Interact with elements by ref (`browser_click`, `browser_type`, `browser_fill_form`)
4. Re-snapshot after navigation or significant DOM changes
5. Capture results (`browser_take_screenshot`, `browser_evaluate`)

## When to use

- Verifying a deployed page renders correctly
- Filling out forms and submitting
- Capturing screenshots for visual confirmation
- Scraping structured data behind JavaScript
- End-to-end testing of user flows

## When NOT to use

- Simple HTTP fetches (use `web_fetch` instead)
- Pure HTML parsing (use a regex/parser, no browser needed)
- Static content extraction without JS rendering

## Tips

- Always snapshot before clicking — refs are session-scoped and change after navigation.
- Prefer semantic locators (role + name) over CSS selectors when possible.
- For authentication-gated flows, save state once and reuse: faster + more reliable.
- Headless by default; pass `--headed` only for debugging.
