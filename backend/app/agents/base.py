"""科研专家的强类型配置结构。角色说明、领域、能力、工具和生成预算均来自 JSON 档案，并在加载时进行边界校验。"""
# 中文模块说明：科研智能体配置与路由模块，负责专家角色定义、配置加载校验、领域匹配和研究工作流约束。

from dataclasses import dataclass
from typing import Any, Dict, Tuple


@dataclass(frozen=True)
class AgentProfile:
    """一个科研专家角色的不可变配置。

    JSON profile 中的领域/技能决定自动路由候选，system_prompt 定义角色工作方式，
    temperature/max_tokens 控制生成预算；selectable=False 用于仅内部调用的合成角色。
    """
    id: str
    name: str
    name_en: str
    focus: str
    focus_en: str
    layer: str
    role: str
    system_prompt: str
    domains: Tuple[str, ...]
    skills: Tuple[str, ...]
    tools: Tuple[str, ...]
    allowed_agents: Tuple[str, ...]
    knowledge_scope: Tuple[str, ...]
    temperature: float = 0.35
    max_tokens: int = 1200
    priority: int = 50
    cost_level: str = "normal"
    review_required: bool = True
    selectable: bool = True

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AgentProfile":
        """验证并规范化 JSON 档案字段，拒绝缺失身份/职责或越界生成参数。"""
        required = ("id", "name", "name_en", "focus", "focus_en", "layer", "role", "system_prompt")
        missing = [key for key in required if not isinstance(data.get(key), str) or not data[key].strip()]
        if missing:
            raise ValueError(f"Agent profile is missing required string fields: {', '.join(missing)}")
        temperature = data.get("temperature", 0.35)
        max_tokens = data.get("max_tokens", 1200)
        priority = data.get("priority", 50)
        if not isinstance(temperature, (int, float)) or not 0 <= temperature <= 2:
            raise ValueError("Agent temperature must be between 0 and 2")
        if not isinstance(max_tokens, int) or not 1 <= max_tokens <= 32768:
            raise ValueError("Agent max_tokens must be between 1 and 32768")
        if not isinstance(priority, int) or not 0 <= priority <= 100:
            raise ValueError("Agent priority must be between 0 and 100")
        list_fields = ("domains", "skills", "tools", "allowed_agents", "knowledge_scope")
        for key in list_fields:
            if key in data and (not isinstance(data[key], list) or any(not isinstance(value, str) for value in data[key])):
                raise ValueError(f"Agent {key} must be a list of strings")
        return cls(
            **{key: data[key].strip() for key in required},
            domains=tuple(data.get("domains", [])),
            skills=tuple(data.get("skills", [])),
            tools=tuple(data.get("tools", [])),
            allowed_agents=tuple(data.get("allowed_agents", [])),
            knowledge_scope=tuple(data.get("knowledge_scope", [])),
            temperature=float(temperature),
            max_tokens=max_tokens,
            priority=priority,
            cost_level=str(data.get("cost_level", "normal")),
            review_required=bool(data.get("review_required", True)),
            selectable=bool(data.get("selectable", True)),
        )

    def to_public_dict(self) -> Dict[str, Any]:
        """输出供前端选择专家的安全字段，保留界面兼容字段但不公开 system_prompt。"""
        return {
            "id": self.id,
            "name": self.name,
            "name_en": self.name_en,
            "focus": self.focus,
            "focus_en": self.focus_en,
            "layer": self.layer,
            "role": self.role,
            "domains": list(self.domains),
            "skills": list(self.skills),
            "tools": list(self.tools),
            "priority": self.priority,
            "cost_level": self.cost_level,
            "review_required": self.review_required,
        }
