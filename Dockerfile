# llmcode — self-hosted CLI coding agent
#
# Build:
#   docker build -t llmcode .
#
# Run (interactive TUI):
#   docker run -it --rm \
#     -v "$PWD:/workspace" \
#     -v "$HOME/.llmcode:/home/llmcode/.llmcode" \
#     --network host \
#     llmcode
#
# Note: --network host lets the container reach your local LLM server
# (vLLM/Ollama/LM Studio) on localhost. On macOS/Windows Docker, replace
# 'localhost' in your config with 'host.docker.internal' instead and
# remove --network host.

FROM python:3.11-slim AS base

# Minimal system deps (git for git tools, curl for healthchecks)
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Non-root user for security
RUN useradd -m -u 1000 -s /bin/bash llmcode

WORKDIR /app

# Install llmcode from PyPI (for reproducibility, pin a version)
RUN pip install --no-cache-dir llmcode-cli

# Switch to non-root user
USER llmcode
WORKDIR /workspace

# Default config dir as volume target
ENV LLMCODE_CONFIG_DIR=/home/llmcode/.llmcode

# Interactive TUI by default
ENTRYPOINT ["llmcode"]
