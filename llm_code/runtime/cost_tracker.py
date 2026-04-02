"""Token cost tracking with model pricing — user-customizable via config."""
from __future__ import annotations
from dataclasses import dataclass

# Built-in fallback pricing per 1M tokens [input, output]
BUILTIN_PRICING: dict[str, tuple[float, float]] = {
    # OpenAI
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4-turbo": (10.00, 30.00),
    "gpt-3.5-turbo": (0.50, 1.50),
    "o3": (2.00, 8.00),
    "o4-mini": (0.50, 2.00),
    # Anthropic
    "claude-opus-4-6": (15.00, 75.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-haiku-4-5": (0.80, 4.00),
    # Google
    "gemini-2.5-pro": (1.25, 10.00),
    "gemini-2.5-flash": (0.15, 0.60),
    # DeepSeek
    "deepseek-chat": (0.27, 1.10),
    "deepseek-reasoner": (0.55, 2.19),
    # xAI
    "grok-3": (3.00, 15.00),
    "grok-3-mini": (0.30, 0.50),
}


@dataclass
class CostTracker:
    model: str = ""
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost_usd: float = 0.0
    custom_pricing: dict | None = None  # from config.json "pricing"

    def add_usage(self, input_tokens: int, output_tokens: int) -> None:
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        in_price, out_price = self._get_pricing()
        self.total_cost_usd += (input_tokens * in_price + output_tokens * out_price) / 1_000_000

    def _get_pricing(self) -> tuple[float, float]:
        # 1. User custom pricing (exact match)
        if self.custom_pricing:
            if self.model in self.custom_pricing:
                p = self.custom_pricing[self.model]
                return (p[0], p[1]) if isinstance(p, list) else (0.0, 0.0)
            # Partial match in custom
            for key, p in self.custom_pricing.items():
                if key != "default" and key in self.model:
                    return (p[0], p[1]) if isinstance(p, list) else (0.0, 0.0)
            # Custom default
            if "default" in self.custom_pricing:
                p = self.custom_pricing["default"]
                return (p[0], p[1]) if isinstance(p, list) else (0.0, 0.0)

        # 2. Built-in pricing (exact match)
        if self.model in BUILTIN_PRICING:
            return BUILTIN_PRICING[self.model]

        # 3. Built-in pricing (partial match)
        model_lower = self.model.lower()
        for key, pricing in BUILTIN_PRICING.items():
            if key in model_lower:
                return pricing

        # 4. Unknown model = free
        return (0.0, 0.0)

    def format_cost(self) -> str:
        lines = [f"Tokens — in: {self.total_input_tokens:,}  out: {self.total_output_tokens:,}"]
        in_price, out_price = self._get_pricing()
        if self.total_cost_usd > 0.0001:
            lines.append(f"  Cost: ${self.total_cost_usd:.4f}")
            lines.append(f"  Rate: ${in_price}/1M in · ${out_price}/1M out")
        elif in_price == 0 and out_price == 0:
            lines.append("  Cost: $0 (free / local model)")
        else:
            lines.append(f"  Cost: ${self.total_cost_usd:.6f}")
        return "  ".join(lines)
