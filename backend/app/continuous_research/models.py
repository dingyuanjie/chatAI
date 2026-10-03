"""Small, dependency-light models for continuous research control."""

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict


DEFAULT_WEIGHTS = {
    "new_evidence": 0.20,
    "new_counterexamples": 0.20,
    "new_predictions": 0.20,
    "new_mathematical_models": 0.15,
    "new_experiments": 0.15,
    "resolved_contradictions": 0.15,
    "new_hypotheses": 0.10,
    "new_claims": 0.05,
}


class StopReason(str, Enum):
    MANUAL_STOP = "MANUAL_STOP"
    MAX_RUNTIME = "MAX_RUNTIME"
    MAX_CYCLES = "MAX_CYCLES"
    NO_INFORMATION_GAIN = "NO_INFORMATION_GAIN"
    RESEARCH_CONVERGED = "RESEARCH_CONVERGED"
    LOOP_DETECTED = "LOOP_DETECTED"
    NO_EXECUTABLE_TASKS = "NO_EXECUTABLE_TASKS"
    ALL_TASKS_BLOCKED = "ALL_TASKS_BLOCKED"
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
    TOO_MANY_ERRORS = "TOO_MANY_ERRORS"
    TOKEN_BUDGET_EXHAUSTED = "TOKEN_BUDGET_EXHAUSTED"
    COST_BUDGET_EXHAUSTED = "COST_BUDGET_EXHAUSTED"
    CALL_BUDGET_EXHAUSTED = "CALL_BUDGET_EXHAUSTED"
    API_QUOTA_EXHAUSTED = "API_QUOTA_EXHAUSTED"
    API_RATE_LIMITED = "API_RATE_LIMITED"
    API_AUTH_FAILED = "API_AUTH_FAILED"
    API_SERVER_ERROR = "API_SERVER_ERROR"
    API_NETWORK_ERROR = "API_NETWORK_ERROR"
    LOCAL_RESOURCE_ERROR = "LOCAL_RESOURCE_ERROR"
    EMERGENCY_STOP = "EMERGENCY_STOP"


@dataclass
class ContinuousResearchConfig:
    max_runtime_minutes: int = 120
    max_cycles: int = 50
    max_consecutive_errors: int = 5
    no_progress_patience: int = 4
    convergence_confirmation_cycles: int = 2
    min_information_gain: float = 0.10
    duplicate_similarity_threshold: float = 0.92
    loop_detection_window: int = 8
    loop_threshold: float = 0.80
    judge_enabled: bool = True
    judge_low_value_threshold: float = 0.20
    final_escape_cycle_enabled: bool = True
    auto_sleep_enabled: bool = True
    provider_type: str = "LOCAL"
    max_model_calls: int | None = None
    max_session_tokens: int | None = None
    max_session_cost: float | None = None
    cost_currency: str = "USD"
    weights: Dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    preset: str = "balanced"

    @classmethod
    def from_dict(cls, values: Dict[str, Any] | None, provider_type: str | None = None) -> "ContinuousResearchConfig":
        data = dict(values or {})
        preset = data.get("preset", "balanced")
        kind = str(provider_type or data.get("provider_type", "LOCAL")).upper()
        if kind not in ("LOCAL", "REMOTE", "HYBRID"):
            kind = "LOCAL"
        defaults = ({"max_runtime_minutes": 180, "max_cycles": 60, "max_model_calls": 250,
                     "max_session_tokens": None, "max_session_cost": None} if kind == "LOCAL" else
                    {"max_runtime_minutes": 120, "max_cycles": 50, "max_model_calls": 100,
                     "max_session_tokens": 500_000, "max_session_cost": 5.0})
        presets = {
            "conservative": {"max_runtime_minutes": 45, "max_cycles": 20, "max_model_calls": 40, "max_session_tokens": 200_000, "max_session_cost": 2.0, "no_progress_patience": 3, "convergence_confirmation_cycles": 1},
            "balanced": defaults,
            "explorer": {"max_runtime_minutes": 240 if kind == "LOCAL" else 180, "max_cycles": 100 if kind == "LOCAL" else 75,
                         "max_model_calls": 250 if kind != "LOCAL" else 500, "max_session_tokens": 1_000_000 if kind != "LOCAL" else None,
                         "max_session_cost": 10.0 if kind != "LOCAL" else None, "no_progress_patience": 6, "convergence_confirmation_cycles": 3},
        }
        merged = {**presets.get(preset, {}), **data, "preset": preset if preset in presets else "balanced"}
        merged["provider_type"] = kind
        allowed = cls.__dataclass_fields__.keys()
        clean = {key: value for key, value in merged.items() if key in allowed}
        config = cls(**clean)
        config.max_runtime_minutes = max(1, min(1440, int(config.max_runtime_minutes)))
        config.max_cycles = max(1, min(1000, int(config.max_cycles)))
        config.max_model_calls = max(1, min(100_000, int(config.max_model_calls))) if config.max_model_calls is not None else None
        config.max_session_tokens = max(1, min(100_000_000, int(config.max_session_tokens))) if config.max_session_tokens is not None else None
        config.max_session_cost = max(0.0, float(config.max_session_cost)) if config.max_session_cost is not None else None
        config.max_consecutive_errors = max(1, min(20, int(config.max_consecutive_errors)))
        config.no_progress_patience = max(2, min(20, int(config.no_progress_patience)))
        config.convergence_confirmation_cycles = max(1, min(10, int(config.convergence_confirmation_cycles)))
        config.min_information_gain = max(0.0, min(1.0, float(config.min_information_gain)))
        config.duplicate_similarity_threshold = max(0.5, min(1.0, float(config.duplicate_similarity_threshold)))
        config.loop_detection_window = max(3, min(20, int(config.loop_detection_window)))
        config.loop_threshold = max(0.5, min(1.0, float(config.loop_threshold)))
        config.judge_low_value_threshold = max(0.0, min(1.0, float(config.judge_low_value_threshold)))
        config.weights = {**DEFAULT_WEIGHTS, **(config.weights or {})}
        return config

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class StopDecision:
    should_stop: bool = False
    should_sleep: bool = False
    next_state: str = "running"
    reason: str = ""
    reason_code: str = ""
    metrics: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
