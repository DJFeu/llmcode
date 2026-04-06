"""Tests for llm_code.runtime.ollama — Ollama API interactions."""
from __future__ import annotations


import httpx
import pytest
import respx

from llm_code.runtime.ollama import OllamaClient, OllamaModel


OLLAMA_BASE = "http://localhost:11434"


class TestOllamaProbe:
    @pytest.mark.asyncio
    @respx.mock
    async def test_probe_success(self) -> None:
        respx.get(f"{OLLAMA_BASE}/api/tags").mock(
            return_value=httpx.Response(200, json={"models": []})
        )
        client = OllamaClient(base_url=OLLAMA_BASE)
        assert await client.probe() is True
        await client.close()

    @pytest.mark.asyncio
    @respx.mock
    async def test_probe_connection_refused(self) -> None:
        respx.get(f"{OLLAMA_BASE}/api/tags").mock(
            side_effect=httpx.ConnectError("Connection refused")
        )
        client = OllamaClient(base_url=OLLAMA_BASE)
        assert await client.probe() is False
        await client.close()

    @pytest.mark.asyncio
    @respx.mock
    async def test_probe_timeout(self) -> None:
        respx.get(f"{OLLAMA_BASE}/api/tags").mock(
            side_effect=httpx.ReadTimeout("timeout")
        )
        client = OllamaClient(base_url=OLLAMA_BASE)
        assert await client.probe() is False
        await client.close()

    @pytest.mark.asyncio
    @respx.mock
    async def test_probe_server_error(self) -> None:
        respx.get(f"{OLLAMA_BASE}/api/tags").mock(
            return_value=httpx.Response(500, text="Internal Server Error")
        )
        client = OllamaClient(base_url=OLLAMA_BASE)
        assert await client.probe() is False
        await client.close()


class TestOllamaListModels:
    @pytest.mark.asyncio
    @respx.mock
    async def test_list_models_success(self) -> None:
        response_data = {
            "models": [
                {
                    "name": "qwen3:1.7b",
                    "size": 1_800_000_000,
                    "details": {
                        "parameter_size": "1.7B",
                        "quantization_level": "Q4_0",
                    },
                },
                {
                    "name": "qwen3.5:4b",
                    "size": 4_200_000_000,
                    "details": {
                        "parameter_size": "4B",
                        "quantization_level": "Q4_K_M",
                    },
                },
            ]
        }
        respx.get(f"{OLLAMA_BASE}/api/tags").mock(
            return_value=httpx.Response(200, json=response_data)
        )
        client = OllamaClient(base_url=OLLAMA_BASE)
        models = await client.list_models()
        assert len(models) == 2
        assert models[0].name == "qwen3:1.7b"
        assert models[0].size_gb == pytest.approx(1.68, abs=0.1)
        assert models[0].parameter_size == "1.7B"
        assert models[0].quantization == "Q4_0"
        assert models[1].name == "qwen3.5:4b"
        await client.close()

    @pytest.mark.asyncio
    @respx.mock
    async def test_list_models_empty(self) -> None:
        respx.get(f"{OLLAMA_BASE}/api/tags").mock(
            return_value=httpx.Response(200, json={"models": []})
        )
        client = OllamaClient(base_url=OLLAMA_BASE)
        models = await client.list_models()
        assert models == []
        await client.close()

    @pytest.mark.asyncio
    @respx.mock
    async def test_list_models_connection_error(self) -> None:
        respx.get(f"{OLLAMA_BASE}/api/tags").mock(
            side_effect=httpx.ConnectError("Connection refused")
        )
        client = OllamaClient(base_url=OLLAMA_BASE)
        models = await client.list_models()
        assert models == []
        await client.close()


class TestOllamaModel:
    def test_estimated_vram_gb(self) -> None:
        model = OllamaModel(
            name="qwen3:1.7b",
            size_gb=1.68,
            parameter_size="1.7B",
            quantization="Q4_0",
        )
        assert model.estimated_vram_gb == pytest.approx(2.0, abs=0.1)

    def test_fits_in_vram(self) -> None:
        model = OllamaModel(
            name="qwen3:1.7b",
            size_gb=1.68,
            parameter_size="1.7B",
            quantization="Q4_0",
        )
        assert model.fits_in_vram(8.0) is True
        assert model.fits_in_vram(1.5) is False

    def test_is_recommended(self) -> None:
        model = OllamaModel(
            name="qwen3:1.7b",
            size_gb=1.68,
            parameter_size="1.7B",
            quantization="Q4_0",
        )
        assert model.is_recommended(8.0) is True
        assert model.is_recommended(2.0) is False


class TestSortModelsForSelection:
    def test_sort_with_vram(self) -> None:
        from llm_code.runtime.ollama import sort_models_for_selection

        models = [
            OllamaModel(name="small", size_gb=1.0, parameter_size="1B", quantization="Q4"),
            OllamaModel(name="big", size_gb=15.0, parameter_size="30B", quantization="Q4"),
            OllamaModel(name="medium", size_gb=3.0, parameter_size="4B", quantization="Q4"),
        ]
        sorted_models = sort_models_for_selection(models, vram_gb=8.0)
        assert sorted_models[0].name == "medium"
        assert sorted_models[1].name == "small"
        assert sorted_models[2].name == "big"

    def test_sort_without_vram(self) -> None:
        from llm_code.runtime.ollama import sort_models_for_selection

        models = [
            OllamaModel(name="big", size_gb=15.0, parameter_size="30B", quantization="Q4"),
            OllamaModel(name="small", size_gb=1.0, parameter_size="1B", quantization="Q4"),
        ]
        sorted_models = sort_models_for_selection(models, vram_gb=None)
        assert sorted_models[0].name == "small"
        assert sorted_models[1].name == "big"
