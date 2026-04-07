"""Per-model query profiler — tracks input/output/cache token usage per model.

Complements the existing CostTracker (which tracks one model at a time and
focuses on budget enforcement) by giving a per-model breakdown across the
entire session, including a /profile slash command formatter.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from llm_code.runtime.cost_tracker import BUILTIN_PRICING


@dataclass
class ModelProfile:
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    call_count: int = 0


def _extract(usage: Any, *names: str) -> int:
    """Pull a field from an object or dict; default 0."""
    for name in names:
        if isinstance(usage, dict):
            if name in usage and usage[name] is not None:
                return int(usage[name])
        else:
            v = getattr(usage, name, None)
            if v is not None:
                return int(v)
    return 0


def _price_for(model: str, table: dict | None) -> tuple[float, float]:
    """Look up (input_per_million, output_per_million) for a model."""
    if table and model in table:
        p = table[model]
        if isinstance(p, (list, tuple)) and len(p) >= 2:
            return float(p[0]), float(p[1])
    if model in BUILTIN_PRICING:
        return BUILTIN_PRICING[model]
    lower = model.lower()
    for key, pricing in BUILTIN_PRICING.items():
        if key in lower:
            return pricing
    return (0.0, 0.0)


@dataclass
class QueryProfiler:
    _profiles: dict[str, ModelProfile] = field(default_factory=dict)

    def record(self, model: str, usage_block: Any) -> None:
        """Record one API call's usage against ``model``."""
        prof = self._profiles.setdefault(model, ModelProfile(model=model))
        prof.input_tokens += _extract(usage_block, "input_tokens", "prompt_tokens")
        prof.output_tokens += _extract(usage_block, "output_tokens", "completion_tokens")
        prof.cache_read_tokens += _extract(usage_block, "cache_read_tokens", "cache_read_input_tokens")
        prof.cache_write_tokens += _extract(usage_block, "cache_write_tokens", "cache_creation_input_tokens")
        prof.call_count += 1

    def per_model_breakdown(self) -> list[ModelProfile]:
        return sorted(self._profiles.values(), key=lambda p: -p.call_count)

    def total_cost(self, cost_table: dict | None = None) -> float:
        total = 0.0
        for prof in self._profiles.values():
            in_price, out_price = _price_for(prof.model, cost_table)
            total += (
                prof.input_tokens * in_price
                + prof.output_tokens * out_price
                + prof.cache_read_tokens * in_price * 0.10
                + prof.cache_write_tokens * in_price * 1.25
            ) / 1_000_000
        return total

    def format_breakdown(self, cost_table: dict | None = None) -> str:
        """Render a /profile-style table."""
        lines = ["Profile this session:", "----------------------"]
        if not self._profiles:
            lines.append("(no API calls yet)")
            return "\n".join(lines)
        for prof in self.per_model_breakdown():
            in_price, out_price = _price_for(prof.model, cost_table)
            cost = (
                prof.input_tokens * in_price + prof.output_tokens * out_price
            ) / 1_000_000
            tag = " (local)" if in_price == 0 and out_price == 0 else ""
            lines.append(
                f"{prof.model:<22} {prof.call_count:>3} calls   "
                f"{prof.input_tokens // 1000}k in / {prof.output_tokens // 1000}k out   "
                f"${cost:.4f}{tag}"
            )
        lines.append("----------------------")
        lines.append(f"Total: ${self.total_cost(cost_table):.4f}")
        return "\n".join(lines)
