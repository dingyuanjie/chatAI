"""Optional token price lookup; absent model entries intentionally mean unknown cost."""

import json
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, Optional

from .models import ModelUsage


PRICING_PATH = Path(__file__).with_name("pricing.json")


class ProviderPricingConfig:
    @staticmethod
    def load(path: Path = PRICING_PATH) -> Dict[str, Dict[str, Any]]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    @classmethod
    def estimate(cls, model: str, usage: Optional[ModelUsage], pricing: Optional[Dict[str, Dict[str, Any]]] = None) -> Optional[ModelUsage]:
        if usage is None or usage.input_tokens is None or usage.output_tokens is None:
            return usage
        rates = (pricing if pricing is not None else cls.load()).get(model)
        if not rates:
            return usage
        cached = min(usage.input_tokens, usage.cached_input_tokens or 0)
        uncached = max(0, usage.input_tokens - cached)
        cost = (uncached * float(rates["input_price_per_million"])
                + cached * float(rates.get("cache_price_per_million", rates["input_price_per_million"]))
                + usage.output_tokens * float(rates["output_price_per_million"])) / 1_000_000
        return replace(usage, estimated_cost=cost, currency=str(rates.get("currency", "USD")))
