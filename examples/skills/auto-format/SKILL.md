---
name: auto-format
description: Automatically format code after writing
auto: true
trigger: format
---

After writing or editing any file, check if a formatter is available:
- Python files: run `ruff format <file>`
- TypeScript/JavaScript: run `npx prettier --write <file>`
- Go: run `gofmt -w <file>`
- Rust: run `rustfmt <file>`

Only format files you just modified. Do not format the entire project.
