"""Cost tracker — accumulates token usage and estimated cost across an agent session."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


# Fallback per-token prices (USD per 1M tokens) when litellm.model_cost is not available.
# These are approximate list prices as of 2025 for common models.
_FALLBACK_PRICES = {
    # Volcengine ARK / Doubao
    "doubao-seed-evolving":        {"input": 0.30, "output": 0.60},
    "doubao-seed-2-1-turbo-260628": {"input": 0.15, "output": 0.30},
    "doubao-seed-2-1-pro-260628":  {"input": 0.50, "output": 2.00},
    "doubao-seed-1-6-flash-250828": {"input": 0.03, "output": 0.06},
    "doubao-seed-2-0-code-preview-260215": {"input": 0.20, "output": 0.60},
    "doubao-seed-2-0-mini-260428": {"input": 0.05, "output": 0.10},
    # Zhipu
    "glm-4-7-251222":              {"input": 0.50, "output": 1.50},
    "glm-5-2-260617":              {"input": 0.60, "output": 2.00},
    # DeepSeek
    "deepseek-v3-2-251201":        {"input": 0.14, "output": 0.28},
}


def _lookup_price(model: str) -> tuple[float, float]:
    """Return (input_price_per_1M, output_price_per_1M) for model, in USD.

    Tries litellm.model_cost first; falls back to _FALLBACK_PRICES by
    stripping provider prefix.  Returns (0, 0) if unknown.
    """
    try:
        import litellm
        info = litellm.model_cost.get(model) or litellm.model_cost.get(model.split("/")[-1])
        if info:
            return (
                float(info.get("input_cost_per_token", 0)) * 1_000_000,
                float(info.get("output_cost_per_token", 0)) * 1_000_000,
            )
    except Exception:
        pass
    # Strip provider prefix ("openai/doubao-..." -> "doubao-...")
    short = model.split("/")[-1]
    if short in _FALLBACK_PRICES:
        p = _FALLBACK_PRICES[short]
        return p["input"], p["output"]
    return 0.0, 0.0


@dataclass
class CostTracker:
    """Accumulates token usage, cost estimates, and tool timing for one session."""

    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_cost_usd: float = 0.0
    tool_calls: int = 0
    tool_duration_ms: int = 0
    llm_calls: int = 0
    _per_model: dict[str, dict] = field(default_factory=dict, repr=False)

    def on_llm_call(self, usage: dict, model: str) -> None:
        """Record token usage from one LLM response."""
        prompt_tok = int(usage.get("prompt_tokens", 0) or 0)
        completion_tok = int(usage.get("completion_tokens", 0) or 0)
        self.total_prompt_tokens += prompt_tok
        self.total_completion_tokens += completion_tok
        self.llm_calls += 1

        in_price, out_price = _lookup_price(model)
        call_cost = (prompt_tok / 1_000_000 * in_price +
                     completion_tok / 1_000_000 * out_price)
        self.total_cost_usd += call_cost

        # Per-model breakdown
        if model not in self._per_model:
            self._per_model[model] = {"prompt": 0, "completion": 0, "cost": 0.0, "calls": 0}
        self._per_model[model]["prompt"] += prompt_tok
        self._per_model[model]["completion"] += completion_tok
        self._per_model[model]["cost"] += call_cost
        self._per_model[model]["calls"] += 1

    def on_tool_call(self, tool_name: str, duration_ms: int) -> None:
        """Record a tool execution."""
        self.tool_calls += 1
        self.tool_duration_ms += max(0, duration_ms)

    def summary(self) -> dict:
        """Return a summary dict suitable for display / logging."""
        return {
            "llm_calls": self.llm_calls,
            "total_tokens": self.total_prompt_tokens + self.total_completion_tokens,
            "prompt_tokens": self.total_prompt_tokens,
            "completion_tokens": self.total_completion_tokens,
            "estimated_cost_usd": round(self.total_cost_usd, 6),
            "tool_calls": self.tool_calls,
            "tool_duration_s": round(self.tool_duration_ms / 1000, 2),
            "per_model": self._per_model,
        }

    def reset(self) -> None:
        """Reset all counters to zero."""
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0
        self.total_cost_usd = 0.0
        self.tool_calls = 0
        self.tool_duration_ms = 0
        self.llm_calls = 0
        self._per_model.clear()

    def format_summary(self) -> str:
        """Human-readable one-line summary."""
        s = self.summary()
        cost_str = f"${s['estimated_cost_usd']:.4f}" if s["estimated_cost_usd"] > 0 else "(price data unavailable)"
        return (
            f"LLM calls: {s['llm_calls']}  "
            f"Tokens: {s['prompt_tokens']}in + {s['completion_tokens']}out  "
            f"Cost: {cost_str}  "
            f"Tools: {s['tool_calls']} calls ({s['tool_duration_s']}s)"
        )
