"""可选的模型 Token 价格估算。

价格从同目录 ``pricing.json`` 读取；未配置价格、用量字段不完整或模型名称不匹配时，
费用保持为 ``None``，不会伪造账单。估算仅供研究运行预算观察，具体费用仍以供应商
账单和实际缓存命中规则为准。配置文件可以在不改动模型适配器的情况下调整。
"""
# 中文模块说明：模型提供方抽象与适配层，统一模型请求、能力描述、用量解析、费用估算和后端工厂选择，避免业务逻辑绑定单一供应商。

import json
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, Optional

from .models import ModelUsage


PRICING_PATH = Path(__file__).with_name("pricing.json")


class ProviderPricingConfig:
    """加载模型价格表，并按输入、缓存输入和输出 Token 分别估算费用。"""

    @staticmethod
    def load(path: Path = PRICING_PATH) -> Dict[str, Dict[str, Any]]:
        """读取 JSON 价格表；文件不存在或内容错误时返回空表以保持请求可用。"""
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    @classmethod
    def estimate(cls, model: str, usage: Optional[ModelUsage], pricing: Optional[Dict[str, Dict[str, Any]]] = None) -> Optional[ModelUsage]:
        """基于每百万 Token 单价生成带币种的估算用量对象。

        缓存输入 Token 不超过总输入量；剩余输入按普通输入价格计费。价格表未命中时
        原样返回供应商报告的用量，调用方据此显示“费用不可用”而不是显示零费用。
        """
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
