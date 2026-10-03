"""连续科研的分层停止策略。

公共策略处理手动/紧急停止、运行时长、轮数、错误上限和多信号收敛；本地策略检查
本地模型可用性与 Token 预算；远端策略额外检查 API 配额、限流、鉴权、调用数、
Token 和可估算费用。策略返回结构化 StopDecision，由控制器决定状态转换与摘要。
"""
# 中文模块说明：连续科研策略组件，提供进展度量、循环/新颖度检测、资源预算和自动停止决策；策略代码保持可独立测试，不直接调用模型服务。

from statistics import mean
from typing import Any, Dict, List, Optional

from .models import ContinuousResearchConfig, StopDecision, StopReason


class CommonResearchPolicy:
    """评估不依赖模型供应商的停止条件与研究收敛信号。"""
    @staticmethod
    def evaluate(config: ContinuousResearchConfig, *, state: str, elapsed_minutes: float,
                 cycles: int, consecutive_errors: int, no_progress_cycles: int,
                 convergence_cycles: int, recent_metrics: List[Dict[str, Any]],
                 manual_stop: bool = False, emergency_stop: bool = False,
                 judge: Optional[Dict[str, Any]] = None) -> StopDecision:
        """先执行人工/紧急停止、运行时长、周期和错误硬限制，再评估软性收敛信号。

        judge 只能影响软性收敛后的重规划选择，不能覆盖任何人工决定或硬上限。
        """
        latest = recent_metrics[-1] if recent_metrics else {}
        base_metrics = {"elapsed_minutes": round(elapsed_minutes, 1), "cycles": cycles,
                        "consecutive_errors": consecutive_errors, "no_progress_cycles": no_progress_cycles,
                        "convergence_cycles": convergence_cycles, "information_gain_score": latest.get("information_gain_score", 0),
                        "novelty_score": latest.get("novelty_score", 0), "loop_score": latest.get("loop_score", 0),
                        "executable_tasks": latest.get("executable_tasks", 0)}
        # 人工停止优先级最高；全局紧急停止次之，然后才检查自动硬性上限。
        if manual_stop:
            return StopDecision(True, False, "stopped", "用户手动停止", "MANUAL_STOP", base_metrics)
        if emergency_stop:
            return StopDecision(True, False, "stopped", "触发全局紧急停止", "EMERGENCY_STOP", base_metrics)
        if elapsed_minutes >= config.max_runtime_minutes:
            return StopDecision(True, False, "stopped", f"达到运行时长上限（{config.max_runtime_minutes} 分钟）", "MAX_RUNTIME", base_metrics)
        if cycles >= config.max_cycles:
            return StopDecision(True, False, "stopped", f"达到研究周期上限（{config.max_cycles} 轮）", "MAX_CYCLES", base_metrics)
        if consecutive_errors >= config.max_consecutive_errors:
            return StopDecision(True, False, "failed", f"连续 {consecutive_errors} 轮执行失败", StopReason.TOO_MANY_ERRORS.value, base_metrics)
        if not config.auto_sleep_enabled:
            return StopDecision(next_state="running", metrics=base_metrics)

        # 收敛观察期间若重新产生有效信息增量，说明研究恢复进展，撤销软性停止趋势。
        if state == "converging" and float(latest.get("information_gain_score", 0)) >= config.min_information_gain:
            return StopDecision(next_state="running", reason="本轮出现新的有效进展，退出收敛确认", reason_code="NEW_PROGRESS", metrics=base_metrics)

        gains = [float(item.get("information_gain_score", 0)) for item in recent_metrics[-config.no_progress_patience:]]
        duplicate_rate = mean(float(item.get("duplicate_rate", 0)) for item in recent_metrics[-config.no_progress_patience:]) if recent_metrics else 0
        low_gain_streak = no_progress_cycles >= config.no_progress_patience
        loop_risk = float(latest.get("loop_score", 0)) >= config.loop_threshold
        graph_blocked = (int(latest.get("executable_tasks", 0)) == 0 and
                         bool(latest.get("blocked_questions")) and
                         len(latest.get("blocked_questions", [])) >= max(1, int(latest.get("new_open_questions", 0))))
        repeated = duplicate_rate >= 0.85
        judge_low = bool(judge and float(judge.get("marginal_value", 1)) < config.judge_low_value_threshold)
        low_average = len(gains) >= min(2, config.no_progress_patience) and mean(gains) < config.min_information_gain
        signals = sum((repeated, loop_risk, graph_blocked, judge_low, low_average))
        base_metrics.update({"duplicate_rate": round(duplicate_rate, 3), "judge_marginal_value": judge.get("marginal_value") if judge else None,
                             "soft_stop_signals": signals})
        if state != "converging" and (low_gain_streak or signals >= 2):
            return StopDecision(False, False, "converging", "连续周期的信息增量偏低，进入收敛确认", "LOW_INFORMATION_GAIN", base_metrics)
        if state == "converging" and convergence_cycles >= config.convergence_confirmation_cycles:
            if judge and judge.get("continue_research") and judge.get("recommended_action") in ("REPLAN", "CHANGE_AGENTS"):
                return StopDecision(False, False, "running", "续行判断发现了可重规划方向", "JUDGE_REPLAN", base_metrics)
            reason_code = (StopReason.LOOP_DETECTED.value if loop_risk else
                           StopReason.ALL_TASKS_BLOCKED.value if graph_blocked else
                           StopReason.NO_EXECUTABLE_TASKS.value if int(latest.get("executable_tasks", 0)) == 0 else
                           StopReason.NO_INFORMATION_GAIN.value)
            return StopDecision(False, True, "sleeping", "连续多个周期未产生显著新信息，自动休眠", reason_code, base_metrics)
        return StopDecision(next_state=state if state == "converging" else "running", metrics=base_metrics)


class LocalResourcePolicy:
    """本地模型资源规则；本地调用不估算费用。"""
    @staticmethod
    def evaluate(config: ContinuousResearchConfig, usage: Dict[str, Any], error: Optional[Dict[str, Any]]) -> Optional[StopDecision]:
        """检查本地模型可用性错误，以及供应商实际报告的累计 Token 是否达到预算。"""
        error_code = str((error or {}).get("error_code") or "")
        if error_code in ("LOCAL_RESOURCE_ERROR", "MODEL_UNAVAILABLE"):
            reason = StopReason.LOCAL_RESOURCE_ERROR if error_code == "LOCAL_RESOURCE_ERROR" else StopReason.MODEL_UNAVAILABLE
            return StopDecision(True, False, "stopped", "本地模型或本地算力资源不可用", reason.value,
                                {"provider_type": "LOCAL", "error_code": error_code})
        total = usage.get("total_tokens")
        if config.max_session_tokens is not None and total is not None and int(total) >= config.max_session_tokens:
            return StopDecision(False, True, "sleeping", "达到本地研究 Token 预算", StopReason.TOKEN_BUDGET_EXHAUSTED.value,
                                {"provider_type": "LOCAL", "total_tokens": total, "token_budget": config.max_session_tokens})
        return None


class RemoteResourcePolicy:
    """远端 API 规则；负责错误归类和调用、Token、可估算费用预算。"""
    @staticmethod
    def evaluate(config: ContinuousResearchConfig, usage: Dict[str, Any], error: Optional[Dict[str, Any]]) -> Optional[StopDecision]:
        """将配额/限流/鉴权/网络错误转为停止原因，再检查各项可用预算。

        只有已知并且币种匹配的费用才参与费用上限判断；用量或价格未知时不假定为零。
        """
        error_code = str((error or {}).get("error_code") or "")
        mapped = {"API_QUOTA_EXHAUSTED": StopReason.API_QUOTA_EXHAUSTED,
                  "REMOTE_RATE_LIMIT": StopReason.API_RATE_LIMITED,
                  "REMOTE_AUTH_ERROR": StopReason.API_AUTH_FAILED,
                  "REMOTE_SERVER_ERROR": StopReason.API_SERVER_ERROR,
                  "REMOTE_NETWORK_ERROR": StopReason.API_NETWORK_ERROR}
        if error_code in mapped:
            reason = mapped[error_code]
            return StopDecision(True, True, "sleeping", f"远端模型调用停止：{reason.value}", reason.value,
                                {"provider_type": "REMOTE", "error_code": error_code})
        calls = int(usage.get("model_calls") or 0)
        if config.max_model_calls is not None and calls >= config.max_model_calls:
            return StopDecision(False, True, "sleeping", "达到远端 API 调用预算", StopReason.CALL_BUDGET_EXHAUSTED.value,
                                {"provider_type": "REMOTE", "model_calls": calls, "call_budget": config.max_model_calls})
        total = usage.get("total_tokens")
        if config.max_session_tokens is not None and total is not None and int(total) >= config.max_session_tokens:
            return StopDecision(False, True, "sleeping", "达到远端 Token 预算", StopReason.TOKEN_BUDGET_EXHAUSTED.value,
                                {"provider_type": "REMOTE", "total_tokens": total, "token_budget": config.max_session_tokens})
        cost = usage.get("estimated_cost")
        if (config.max_session_cost is not None and cost is not None
                and str(usage.get("currency") or config.cost_currency).upper() == config.cost_currency.upper()
                and float(cost) >= config.max_session_cost):
            return StopDecision(False, True, "sleeping", "达到远端费用预算", StopReason.COST_BUDGET_EXHAUSTED.value,
                                {"provider_type": "REMOTE", "estimated_cost": cost, "currency": usage.get("currency"), "cost_budget": config.max_session_cost})
        return None


class StopPolicyEngine:
    """公共停止规则与模型来源专属资源规则的统一决策入口。"""
    @staticmethod
    def evaluate(config: ContinuousResearchConfig, *, state: str, elapsed_minutes: float,
                 cycles: int, consecutive_errors: int, no_progress_cycles: int,
                 convergence_cycles: int, recent_metrics: List[Dict[str, Any]],
                 manual_stop: bool = False, emergency_stop: bool = False,
                 judge: Optional[Dict[str, Any]] = None, provider_type: str = "LOCAL",
                 resource_usage: Optional[Dict[str, Any]] = None,
                 latest_error: Optional[Dict[str, Any]] = None) -> StopDecision:
        """公共策略若产生硬性终止就立即返回，否则按 LOCAL/REMOTE/HYBRID 选择资源策略。"""
        common = CommonResearchPolicy.evaluate(config, state=state, elapsed_minutes=elapsed_minutes,
            cycles=cycles, consecutive_errors=consecutive_errors, no_progress_cycles=no_progress_cycles,
            convergence_cycles=convergence_cycles, recent_metrics=recent_metrics,
            manual_stop=manual_stop, emergency_stop=emergency_stop, judge=judge)
        if common.reason_code in {StopReason.MANUAL_STOP.value, StopReason.EMERGENCY_STOP.value,
                                  StopReason.MAX_RUNTIME.value, StopReason.MAX_CYCLES.value,
                                  "MAX_CONSECUTIVE_ERRORS", StopReason.TOO_MANY_ERRORS.value}:
            return common
        policy = RemoteResourcePolicy if provider_type.upper() in ("REMOTE", "HYBRID") else LocalResourcePolicy
        resource_decision = policy.evaluate(config, resource_usage or {}, latest_error)
        return resource_decision or common
