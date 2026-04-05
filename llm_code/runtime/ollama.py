"""Ollama API client for probe, model listing, and selection helpers."""
from __future__ import annotations

from dataclasses import dataclass

import httpx


@dataclass(frozen=True)
class OllamaModel:
    """Represents a locally available Ollama model."""

    name: str
    size_gb: float
    parameter_size: str
    quantization: str

    @property
    def estimated_vram_gb(self) -> float:
        """Estimated runtime VRAM: disk size * 1.2 for KV cache + buffers."""
        return self.size_gb * 1.2

    def fits_in_vram(self, available_gb: float) -> bool:
        return self.estimated_vram_gb <= available_gb

    def is_recommended(self, available_gb: float) -> bool:
        """Fits within 90% of available VRAM."""
        return self.estimated_vram_gb <= available_gb * 0.9


class OllamaClient:
    """Thin async client for Ollama's native API."""

    def __init__(self, base_url: str = "http://localhost:11434") -> None:
        self._base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(2.0))

    async def probe(self) -> bool:
        """Check if Ollama is reachable."""
        try:
            resp = await self._client.get(f"{self._base_url}/api/tags")
            return resp.status_code == 200
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout, OSError):
            return False

    async def list_models(self) -> list[OllamaModel]:
        """Fetch locally available models from Ollama."""
        try:
            resp = await self._client.get(f"{self._base_url}/api/tags")
            if resp.status_code != 200:
                return []
            data = resp.json()
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout, OSError, ValueError):
            return []

        models: list[OllamaModel] = []
        for entry in data.get("models", []):
            details = entry.get("details", {})
            size_bytes = entry.get("size", 0)
            models.append(
                OllamaModel(
                    name=entry.get("name", ""),
                    size_gb=size_bytes / (1024**3),
                    parameter_size=details.get("parameter_size", ""),
                    quantization=details.get("quantization_level", ""),
                )
            )
        return models

    async def close(self) -> None:
        await self._client.aclose()


def sort_models_for_selection(
    models: list[OllamaModel],
    vram_gb: float | None,
) -> list[OllamaModel]:
    """Sort models for the interactive selector.

    With VRAM info: models that fit sorted descending (biggest first),
    then models that don't fit sorted ascending (smallest overshoot first).
    Without VRAM info: sorted ascending by size.
    """
    if vram_gb is None:
        return sorted(models, key=lambda m: m.size_gb)

    fits = [m for m in models if m.fits_in_vram(vram_gb)]
    exceeds = [m for m in models if not m.fits_in_vram(vram_gb)]

    fits.sort(key=lambda m: m.size_gb, reverse=True)
    exceeds.sort(key=lambda m: m.size_gb)

    return fits + exceeds
