---
name: agent-browser
description: Use when the user needs browser automation via the agent-browser CLI for web testing, form filling, screenshots, or data extraction. Alternative to Playwright when the agent-browser CLI is preferred.
auto: false
tags: [browser, testing, automation, cli]
---

# Browser Automation with agent-browser

A CLI-based alternative to Playwright. Use when the user explicitly prefers
`agent-browser` or when an MCP server is unavailable.

## Quick start

```bash
agent-browser open <url>        # Navigate to page
agent-browser snapshot -i       # Get interactive elements with refs
agent-browser click @e1         # Click element by ref
agent-browser fill @e2 "text"   # Fill input by ref
agent-browser close             # Close browser
```

## Core workflow

1. Navigate: `agent-browser open <url>`
2. Snapshot: `agent-browser snapshot -i` (returns elements with refs like `@e1`, `@e2`)
3. Interact using refs from the snapshot
4. Re-snapshot after navigation or significant DOM changes

## Common commands

### Navigation
```bash
agent-browser open <url>
agent-browser back
agent-browser forward
agent-browser reload
agent-browser close
```

### Interactions
```bash
agent-browser click @e1
agent-browser fill @e2 "text"
agent-browser type @e2 "text"      # type without clearing
agent-browser press Enter
agent-browser press Control+a
agent-browser hover @e1
agent-browser check @e1
agent-browser select @e1 "value"
agent-browser scroll down 500
agent-browser drag @e1 @e2
agent-browser upload @e1 file.pdf
```

### Information
```bash
agent-browser get text @e1
agent-browser get value @e1
agent-browser get attr @e1 href
agent-browser get title
agent-browser get url
agent-browser get count ".item"
```

### Screenshots & PDF
```bash
agent-browser screenshot path.png
agent-browser screenshot --full
agent-browser pdf output.pdf
```

### Wait
```bash
agent-browser wait @e1
agent-browser wait --text "Success"
agent-browser wait --url "**/dashboard"
agent-browser wait --load networkidle
```

### Semantic locators (alternative to refs)
```bash
agent-browser find role button click --name "Submit"
agent-browser find text "Sign In" click
agent-browser find label "Email" fill "user@test.com"
```

### Sessions & profiles
```bash
agent-browser --session test1 open site-a.com
agent-browser --profile ~/.myapp-profile open myapp.com
```

## Example: Form submission

```bash
agent-browser open https://example.com/form
agent-browser snapshot -i
# Output: textbox "Email" [ref=e1], textbox "Password" [ref=e2], button "Submit" [ref=e3]
agent-browser fill @e1 "user@example.com"
agent-browser fill @e2 "password123"
agent-browser click @e3
agent-browser wait --load networkidle
```

## Installation

```bash
bun add -g agent-browser
# Then install Chromium via Playwright as fallback if needed:
cd /tmp && bun init -y && bun add playwright && bun playwright install chromium
```

Run `agent-browser --help` for the full command reference.
