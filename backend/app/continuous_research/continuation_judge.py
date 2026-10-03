"""可选的模型续行评审。模型只提供软性“继续/重规划/休眠”建议，不能覆盖人工指令、紧急停止或资源硬上限。"""
# 中文模块说明：连续科研策略组件，提供进展度量、循环/新颖度检测、资源预算和自动停止决策；策略代码保持可独立测试，不直接调用模型服务。

import json
import re
from typing import Any, Callable, Dict


class ResearchContinuationJudge:
    """把模型续行意见限制为可校验的 JSON 建议，不允许它绕过硬性预算。"""
    @staticmethod
    def parse(response: str) -> Dict[str, Any]:
        """从可能带 Markdown 围栏的输出中提取 JSON，校验动作枚举并限制边际价值范围。"""
        text = re.sub(r"^```(?:json)?|```$", "", (response or "").strip(), flags=re.IGNORECASE | re.MULTILINE).strip()
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            raise ValueError("continuation judge did not return JSON")
        value = json.loads(match.group(0))
        action = value.get("recommended_action", "CONTINUE")
        if action not in {"CONTINUE", "REPLAN", "CHANGE_AGENTS", "SLEEP"}:
            action = "CONTINUE"
        return {"continue_research": bool(value.get("continue_research", True)),
                "marginal_value": max(0.0, min(1.0, float(value.get("marginal_value", 0.5)))),
                "reason": str(value.get("reason", ""))[:500], "recommended_action": action}

    @classmethod
    def evaluate(cls, context: Dict[str, Any], call_model: Callable[[str, str, float, int], str]) -> Dict[str, Any]:
        """把近期进展交给评审模型，解析其是否值得继续及建议动作并返回标准字段。"""
        system = "你是研究续行判断器，不判断理论真伪。仅判断再运行一轮是否值得。必须只返回 JSON：continue_research(boolean), marginal_value(0到1), reason(string), recommended_action(CONTINUE|REPLAN|CHANGE_AGENTS|SLEEP)。"
        prompt = "评估最近研究周期，避免把改写旧结论当作进展。\n" + json.dumps(context, ensure_ascii=False)[:12000]
        return cls.parse(call_model(system, prompt, 0.1, 600))
