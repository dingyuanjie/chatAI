"""Optional continuation judge. Its answer is advisory and never overrides hard limits."""

import json
import re
from typing import Any, Callable, Dict


class ResearchContinuationJudge:
    @staticmethod
    def parse(response: str) -> Dict[str, Any]:
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
        system = "你是研究续行判断器，不判断理论真伪。仅判断再运行一轮是否值得。必须只返回 JSON：continue_research(boolean), marginal_value(0到1), reason(string), recommended_action(CONTINUE|REPLAN|CHANGE_AGENTS|SLEEP)。"
        prompt = "评估最近研究周期，避免把改写旧结论当作进展。\n" + json.dumps(context, ensure_ascii=False)[:12000]
        return cls.parse(call_model(system, prompt, 0.1, 600))

