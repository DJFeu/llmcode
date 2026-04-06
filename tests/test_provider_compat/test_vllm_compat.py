"""vLLM-specific compatibility tests."""
import pytest
import httpx


class TestVLLMBasic:
    def test_models_endpoint(self, vllm_url):
        """vLLM should expose /v1/models."""
        resp = httpx.get(f"{vllm_url}/models", timeout=5)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["data"]) > 0

    def test_chat_completion(self, vllm_url):
        """Basic chat completion should work."""
        resp = httpx.post(
            f"{vllm_url}/chat/completions",
            json={
                "model": httpx.get(f"{vllm_url}/models", timeout=5).json()["data"][0]["id"],
                "messages": [{"role": "user", "content": "Say OK"}],
                "max_tokens": 10,
            },
            timeout=60,
        )
        assert resp.status_code == 200
        content = resp.json()["choices"][0]["message"]["content"]
        assert len(content) > 0

    def test_thinking_disable(self, vllm_url):
        """Disabling thinking should produce clean output."""
        model = httpx.get(f"{vllm_url}/models", timeout=5).json()["data"][0]["id"]
        resp = httpx.post(
            f"{vllm_url}/chat/completions",
            json={
                "model": model,
                "messages": [{"role": "user", "content": "Say hi"}],
                "max_tokens": 50,
                "chat_template_kwargs": {"enable_thinking": False},
            },
            timeout=60,
        )
        assert resp.status_code == 200
        content = resp.json()["choices"][0]["message"]["content"]
        assert "Thinking" not in content
        assert "<think>" not in content

    def test_context_window_detection(self, vllm_url):
        """max_model_len should be detectable from /v1/models."""
        data = httpx.get(f"{vllm_url}/models", timeout=5).json()
        mml = data["data"][0].get("max_model_len", 0)
        assert mml > 0, "max_model_len should be reported"


class TestVLLMToolCalling:
    def test_native_tools_fallback(self, vllm_url):
        """If native tools not supported, should get an error we can handle."""
        model = httpx.get(f"{vllm_url}/models", timeout=5).json()["data"][0]["id"]
        resp = httpx.post(
            f"{vllm_url}/chat/completions",
            json={
                "model": model,
                "messages": [{"role": "user", "content": "Hello"}],
                "max_tokens": 10,
                "tools": [{"type": "function", "function": {"name": "test", "parameters": {}}}],
            },
            timeout=60,
        )
        # Either 200 (supports tools) or 400/422 (doesn't — we handle this)
        assert resp.status_code in (200, 400, 422)
