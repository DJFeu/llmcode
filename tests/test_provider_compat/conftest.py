"""Provider compatibility test configuration.

These tests require a running LLM server. Skip if unavailable.
Set environment variables to configure:
  LLMCODE_TEST_VLLM_URL=http://localhost:8000/v1
  LLMCODE_TEST_OLLAMA_URL=http://localhost:11434/v1
"""
import os
import pytest
import httpx


def _check_provider(url: str) -> bool:
    try:
        resp = httpx.get(f"{url}/models", timeout=3)
        return resp.status_code == 200
    except Exception:
        return False


@pytest.fixture
def vllm_url():
    url = os.environ.get("LLMCODE_TEST_VLLM_URL", "http://localhost:8000/v1")
    if not _check_provider(url):
        pytest.skip(f"vLLM not available at {url}")
    return url


@pytest.fixture
def ollama_url():
    url = os.environ.get("LLMCODE_TEST_OLLAMA_URL", "http://localhost:11434/v1")
    if not _check_provider(url):
        pytest.skip(f"Ollama not available at {url}")
    return url
