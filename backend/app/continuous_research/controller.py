"""连续科研决策控制器。

本模块把研究过程中的轮次、进展指标、资源统计和模型错误整理为统一的策略输入，
再交给停止策略引擎判断“继续运行、进入收敛观察、自动休眠或停止”。这里负责
组装和转发上下文，不在控制器中重复实现各类硬性上限与软性收敛规则。
"""
# 中文模块说明：连续科研策略组件，提供进展度量、循环/新颖度检测、资源预算和自动停止决策；策略代码保持可独立测试，不直接调用模型服务。

from typing import Any, Dict, List, Optional

from .models import ContinuousResearchConfig, StopDecision
from .progress_tracker import ProgressTracker
from .stop_policy import StopPolicyEngine


class ContinuousResearchController:
    """为一次科研运行提供进展度量器和自动停止决策入口。"""

    def __init__(self, config: ContinuousResearchConfig, embed=None):
        """保存本次运行配置，并按配置权重创建进展分析器。

        ``embed`` 是可选的向量化回调；未提供时，进展分析器使用词面相似度，
        因此连续科研的停止判断不依赖某个特定向量服务。
        """
        self.config = config
        self.progress = ProgressTracker(config.weights, config.duplicate_similarity_threshold, embed)

    def decide(self, *, state: str, elapsed_minutes: float, cycles: int, consecutive_errors: int,
               no_progress_cycles: int, convergence_cycles: int, recent_metrics: List[Dict[str, Any]],
               manual_stop: bool = False, emergency_stop: bool = False,
               judge: Optional[Dict[str, Any]] = None, provider_type: str = "LOCAL",
               resource_usage: Optional[Dict[str, Any]] = None,
               latest_error: Optional[Dict[str, Any]] = None) -> StopDecision:
        """把运行快照传给策略引擎并返回结构化决策。

        手动停止、全局紧急停止、时长/轮次、模型错误、Token 与费用用量等信息
        都通过显式参数传递，便于策略层独立测试和未来增加新的模型后端。
        """
        return StopPolicyEngine.evaluate(self.config, state=state, elapsed_minutes=elapsed_minutes,
            cycles=cycles, consecutive_errors=consecutive_errors, no_progress_cycles=no_progress_cycles,
            convergence_cycles=convergence_cycles, recent_metrics=recent_metrics,
            manual_stop=manual_stop, emergency_stop=emergency_stop, judge=judge,
            provider_type=provider_type, resource_usage=resource_usage, latest_error=latest_error)

    @staticmethod
    def escape_outcome(found_new_direction: bool) -> str:
        """把最终反例探索结果转换为运行状态：有新方向则续跑，否则休眠。"""
        return "running" if found_new_direction else "sleeping"
