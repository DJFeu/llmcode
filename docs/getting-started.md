# Getting Started

## Prerequisites

- Python 3.11+
- A running LLM server (vLLM, Ollama, LM Studio, or OpenAI API)

## Installation

```bash
pip install llm-code
```

## First Run

```bash
# Set up config
mkdir -p ~/.llmcode
echo '{"model": "your-model", "provider": {"base_url": "http://localhost:8000/v1"}}' > ~/.llmcode/config.json

# Start interactive mode
llm-code
```

## One-Shot Mode

```bash
llm-code "explain what this project does"
cat error.log | llm-code "fix this error"
llm-code --budget 100000 "refactor the auth module"
```

## Key Commands

| Command | Description |
|---------|-------------|
| `/help` | Show all commands |
| `/plugin search github` | Find plugins |
| `/undo` | Undo last file change |
| `/memory set arch "microservices"` | Remember something |
