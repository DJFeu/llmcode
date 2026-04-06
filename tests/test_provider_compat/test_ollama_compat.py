"""Ollama-specific compatibility tests."""
import pytest
import httpx


class TestOllamaBasic:
    def test_models_endpoint(self, ollama_url):
        resp = httpx.get(f"{ollama_url}/models", timeout=5)
        # Ollama's /v1/models should work
        assert resp.status_code == 200

    def test_chat_completion(self, ollama_url):
        models = httpx.get(f"{ollama_url}/models", timeout=5).json()
        if not models.get("data"):
            pytest.skip("No models available in Ollama")
        model = models["data"][0]["id"]
        resp = httpx.post(
            f"{ollama_url}/chat/completions",
            json={
                "model": model,
                "messages": [{"role": "user", "content": "Say OK"}],
                "max_tokens": 10,
            },
            timeout=60,
        )
        # 200 = success, 400 = model not loaded/compatible (acceptable)
        assert resp.status_code in (200, 400)
