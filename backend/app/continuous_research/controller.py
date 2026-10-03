"""Orchestrates deterministic progress and continuation policy inputs."""

from typing import Any, Dict, List, Optional

from .models import ContinuousResearchConfig, StopDecision
from .progress_tracker import ProgressTracker
from .stop_policy import StopPolicyEngine


class ContinuousResearchController:
    def __init__(self, config: ContinuousResearchConfig, embed=None):
        self.config = config
        self.progress = ProgressTracker(config.weights, config.duplicate_similarity_threshold, embed)

    def decide(self, *, state: str, elapsed_minutes: float, cycles: int, consecutive_errors: int,
               no_progress_cycles: int, convergence_cycles: int, recent_metrics: List[Dict[str, Any]],
               manual_stop: bool = False, emergency_stop: bool = False,
               judge: Optional[Dict[str, Any]] = None, provider_type: str = "LOCAL",
               resource_usage: Optional[Dict[str, Any]] = None,
               latest_error: Optional[Dict[str, Any]] = None) -> StopDecision:
        return StopPolicyEngine.evaluate(self.config, state=state, elapsed_minutes=elapsed_minutes,
            cycles=cycles, consecutive_errors=consecutive_errors, no_progress_cycles=no_progress_cycles,
            convergence_cycles=convergence_cycles, recent_metrics=recent_metrics,
            manual_stop=manual_stop, emergency_stop=emergency_stop, judge=judge,
            provider_type=provider_type, resource_usage=resource_usage, latest_error=latest_error)

    @staticmethod
    def escape_outcome(found_new_direction: bool) -> str:
        return "running" if found_new_direction else "sleeping"
