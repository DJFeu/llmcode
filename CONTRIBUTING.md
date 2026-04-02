# Contributing to llm-code

## Development Setup

```bash
git clone https://github.com/user/llm-code
cd llm-code
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Running Tests

```bash
pytest                          # all tests
pytest -x                       # stop on first failure
pytest --cov=llm_code           # with coverage
pytest tests/test_tools/        # specific directory
```

## Code Style

- Formatter/linter: `ruff check --fix llm_code/ tests/`
- Type hints on all function signatures
- Frozen dataclasses for immutable data
- Max file size: 400 lines (split if larger)

## Pull Request Process

1. Create a feature branch
2. Write tests first (TDD)
3. Run `pytest` and `ruff check`
4. Submit PR with clear description

## Architecture

```
cli → runtime → {tools, api}
         ↓
      mcp, marketplace, lsp
```

- `api/` — Provider abstraction (no other deps)
- `tools/` — Tool system (does not depend on api)
- `runtime/` — Combines api + tools
- `cli/` — Only talks to runtime

## Adding a New Tool

1. Create `llm_code/tools/my_tool.py`
2. Extend `Tool` ABC
3. Add Pydantic input model
4. Implement safety methods (is_read_only, is_destructive)
5. Register in `cli/app.py`
6. Add tests in `tests/test_tools/`

## Adding an MCP Server

Add to `~/.llm-code/config.json`:

```json
{
  "mcpServers": {
    "my-server": {
      "command": "npx",
      "args": ["-y", "my-mcp-server"]
    }
  }
}
```
