"""Typed configuration shared by configurable research roles."""

from dataclasses import dataclass
from typing import Any, Dict, Tuple


@dataclass(frozen=True)
class AgentProfile:
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
        """Return a JSON-ready payload while preserving the legacy UI fields."""
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
