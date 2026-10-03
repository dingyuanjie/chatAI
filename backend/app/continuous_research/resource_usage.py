"""Provider-neutral resource counters for a research session."""

from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional


@dataclass
class ResearchResourceUsage:
    runtime_seconds: float = 0.0
    cycles: int = 0
    model_calls: int = 0
    successful_calls: int = 0
    failed_calls: int = 0
    retry_count: int = 0
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    estimated_cost: Optional[float] = None
    currency: Optional[str] = None
    gpu_memory_peak: Optional[float] = None
    gpu_utilization: Optional[float] = None
    cpu_utilization: Optional[float] = None
    memory_usage: Optional[float] = None
    information_gain: float = 0.0

    @classmethod
    def from_aggregate(cls, value: Dict[str, Any]) -> "ResearchResourceUsage":
        fields = cls.__dataclass_fields__
        return cls(**{key: item for key, item in value.items() if key in fields})

    def research_efficiency(self, provider_type: str) -> Optional[float]:
        """Normalized gain per blended resource unit; useful for trend detection, not billing."""
        if not self.information_gain:
            return 0.0
        token_units = (self.total_tokens or 0) / 100_000
        if str(provider_type).upper() == "REMOTE" and self.estimated_cost is not None:
            consumption = 1.0 + self.model_calls / 10 + token_units + self.estimated_cost
        else:
            runtime_units = self.runtime_seconds / 3600
            consumption = 1.0 + self.model_calls / 10 + token_units + runtime_units
        return round(self.information_gain / consumption, 4)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
