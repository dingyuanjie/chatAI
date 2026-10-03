"""与模型供应商无关的研究资源汇总结构，包含运行时长、周期、调用、Token、费用及研究信息增量。未知字段保持为空，避免伪造精度。"""
# 中文模块说明：连续科研策略组件，提供进展度量、循环/新颖度检测、资源预算和自动停止决策；策略代码保持可独立测试，不直接调用模型服务。

from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional


@dataclass
class ResearchResourceUsage:
    """一次科研任务的资源快照；供应商未报告的数据保持 None，不虚构统计值。"""
    """集中汇总科研任务的模型调用数、Token、估算费用和运行时长，供预算停止策略及界面展示使用。"""
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
        """只复制数据类已声明字段，忽略 API 聚合对象中的展示扩展字段。"""
        """把aggregate来源的数据转换为当前对象，并在边界处整理字段格式。"""
        fields = cls.__dataclass_fields__
        return cls(**{key: item for key, item in value.items() if key in fields})

    def research_efficiency(self, provider_type: str) -> Optional[float]:
        """按信息增量与模型调用、Token、时长或远端费用计算归一化趋势指标。此数值用于观察研究资源效率，不代表账单价格或科学结论质量。"""
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
        """将资源字段序列化为可返回 API 的字典。"""
        """把当前对象转换为dict格式，供调用方稳定地读取或传输。"""
        return asdict(self)
