# Ollama DX Improvements Design Spec

**Date:** 2026-04-06
**Status:** Approved
**Scope:** 4 DX features to improve Ollama backend experience

---

## Overview

llm-code already supports Ollama via `OpenAICompatProvider`, but requires users to manually specify `--api http://localhost:11434/v1`. These 4 features reduce that friction to zero-config.

## Feature 1: `--provider ollama` CLI Shortcut

### What
New `--provider` CLI option. `--provider ollama` is equivalent to `--api http://localhost:11434/v1`.

### Changes
- **`llm_code/cli/tui_main.py`**: Add `--provider` option with choice `["ollama"]`
- When `--provider ollama` is set and `--api` is not, inject `base_url = "http://localhost:11434/v1"` into `cli_overrides`
- `--api` takes precedence over `--provider` (explicit wins)
- Future providers (lmstudio, llama.cpp) can be added to the choice list

### Config file support
Also supported in `config.json`:
```json
{
  "provider": {
    "name": "ollama",
    "base_url": "http://localhost:11434/v1"
  }
}
```
The `name` field is informational when `base_url` is explicitly set. When `name` is "ollama" and `base_url` is absent, the default Ollama URL is used.

## Feature 2: Ollama Connection Probe

### What
On startup with `--provider ollama`, verify Ollama is reachable before entering TUI.

### Changes
- **`llm_code/runtime/ollama.py`** (new): `OllamaClient` class
  - `async probe() -> bool`: `GET http://localhost:11434/api/tags` with 2s timeout
  - `async list_models() -> list[OllamaModel]`: Parse response from `/api/tags`
  - `OllamaModel` dataclass: `name: str`, `size_gb: float`, `parameter_size: str`, `quantization: str`
- **`llm_code/cli/tui_main.py`**: Before TUI launch, if provider=ollama, run probe. On failure, print actionable error and exit:
  ```
  Error: Cannot connect to Ollama at localhost:11434
  Make sure Ollama is running: ollama serve
  ```

### Design decisions
- Probe uses the Ollama-native `/api/tags` endpoint (not `/v1/models`) because it returns richer metadata (size, quantization level)
- 2-second timeout: fast enough for local, long enough for slow cold-start
- Probe runs once at startup, not continuously

## Feature 3: Interactive Model Selector

### What
When `--provider ollama` is used without `--model`, display an interactive model selector in the terminal before launching TUI.

### Trigger conditions
- `--provider ollama` AND no `--model` specified AND no `model` in config

### Changes
- **`llm_code/runtime/ollama.py`**: `list_models()` returns models sorted by hardware recommendation (see Feature 4)
- **`llm_code/cli/tui_main.py`**: New function `select_ollama_model(models, vram_gb)`:
  - Displays numbered list in terminal with VRAM annotations
  - User types number to select
  - Returns selected model name
  - Example output:
    ```
    Available Ollama models:

      ★ 1) qwen3.5:4b     (~4GB)  Recommended
        2) qwen3:4b        (~3GB)
        3) qwen3:1.7b      (~2GB)
        4) qwen3.5:32b     (~20GB) ⚠️ May exceed available VRAM

    Select model [1]:
    ```

### Edge cases
- No models downloaded: Print error with `ollama pull` instructions, exit
- Only one model: Auto-select it, print confirmation
- User presses Enter with no input: Select the recommended model (first in list)

## Feature 4: Hardware-Aware Recommendations

### What
Detect available VRAM/memory and use it to sort and annotate the model selector.

### Changes
- **`llm_code/runtime/hardware.py`** (new): `detect_vram() -> float | None`
  - Detection chain (first success wins):
    1. **NVIDIA GPU**: `nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits` → parse MB → convert to GB
    2. **Apple Silicon**: `sysctl -n hw.memsize` → total unified memory in bytes → convert to GB, apply 0.75 factor (not all memory available for ML)
    3. **Linux fallback**: Read `/proc/meminfo` MemTotal → convert to GB, apply 0.5 factor
    4. **None**: Detection failed, skip recommendations
  - All detection via `subprocess.run` with 2s timeout, no external dependencies

### Model size estimation
- Parse from Ollama's `/api/tags` response: `size` field (bytes on disk)
- Heuristic: runtime VRAM ≈ `size_on_disk * 1.2` (overhead for KV cache, runtime buffers)
- This is approximate but sufficient for sorting and warnings

### Sorting logic
Given `available_vram_gb`:
1. Models that fit in VRAM: sorted by size descending (biggest that fits = best quality)
2. Models that exceed VRAM: sorted by size ascending (least over = most likely to still work with offloading)
3. Recommended model (★): largest model that fits within 90% of available VRAM
4. Warning (⚠️): models exceeding available VRAM

When VRAM detection fails (`None`): show models sorted by size ascending, no ★ or ⚠️ annotations.

## File Summary

| File | Action | Description |
|------|--------|-------------|
| `llm_code/runtime/ollama.py` | New | OllamaClient: probe, list_models |
| `llm_code/runtime/hardware.py` | New | VRAM/memory detection |
| `llm_code/cli/tui_main.py` | Modify | --provider option, model selector, probe |
| `llm_code/runtime/model_aliases.py` | Modify | Add Ollama model aliases |
| `tests/test_runtime/test_ollama.py` | New | Tests for OllamaClient |
| `tests/test_runtime/test_hardware.py` | New | Tests for VRAM detection |
| `tests/test_cli/test_provider_ollama.py` | New | Tests for CLI --provider flow |

## Not in scope

- Auto-installing Ollama or downloading models
- Background/continuous health monitoring
- Changes to `OpenAICompatProvider` (already compatible)
- Ollama-specific streaming optimizations
- Config UI for Ollama settings in TUI
