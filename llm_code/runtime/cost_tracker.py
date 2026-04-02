"""Token cost tracking with model pricing."""
from __future__ import annotations
from dataclasses import dataclass, field

# Pricing per 1M tokens (USD)
MODEL_PRICING: dict[str, tuple[float, float]] = {
    # (input_per_1M, output_per_1M)
    # OpenAI
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4-turbo": (10.00, 30.00),
    "gpt-3.5-turbo": (0.50, 1.50),
    # Anthropic
    "claude-opus-4-6": (15.00, 75.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-haiku-4-5": (0.80, 4.00),
    # Local models (free)
    "local": (0.0, 0.0),
}


@dataclass
class CostTracker:
    model: str = ""
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost_usd: float = 0.0

    def add_usage(self, input_tokens: int, output_tokens: int) -> None:
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        in_price, out_price = self._get_pricing()
        self.total_cost_usd += (input_tokens * in_price + output_tokens * out_price) / 1_000_000

    def _get_pricing(self) -> tuple[float, float]:
        # Exact match
        if self.model in MODEL_PRICING:
            return MODEL_PRICING[self.model]
        # Partial match
        model_lower = self.model.lower()
        for key, pricing in MODEL_PRICING.items():
            if key in model_lower:
                return pricing
        # Local models are free
        return (0.0, 0.0)

    def format_cost(self) -> str:
        parts = [
            f"Tokens — in: {self.total_input_tokens:,}  out: {self.total_output_tokens:,}",
        ]
        if self.total_cost_usd > 0:
            parts.append(f"  Cost: ${self.total_cost_usd:.4f}")
        else:
            parts.append("  Cost: $0 (local model)")
        return "  ".join(parts)
