"""Named research modes and their bounded role/stage guidance."""

from dataclasses import dataclass
from typing import Dict, Tuple


@dataclass(frozen=True)
class WorkflowProfile:
    id: str
    name: str
    name_en: str
    instruction: str
    initial_agents: Tuple[str, ...]
    required_agents: Tuple[str, ...]

    def to_public_dict(self) -> Dict[str, object]:
        return {"id": self.id, "name": self.name, "name_en": self.name_en}


WORKFLOWS: Dict[str, WorkflowProfile] = {
    "expert_consultation": WorkflowProfile(
        "expert_consultation", "专家咨询", "Expert consultation",
        "聚焦回答一个明确的专业问题，给出关键依据与主要限制，避免扩展无关分支。",
        ("physics", "math", "complexity", "cosmology", "foundations"), (),
    ),
    "multidisciplinary": WorkflowProfile(
        "multidisciplinary", "多学科研究", "Multidisciplinary research",
        "从多个相关领域独立分析，再比较一致发现、真实分歧、证据强弱和未解问题。",
        ("physics", "math", "complexity", "cosmology", "foundations", "critic"), ("critic",),
    ),
    "theory_attack": WorkflowProfile(
        "theory_attack", "理论攻击", "Theory attack",
        "优先尝试反驳待审主张：查找反例、替代理论、逻辑缺口和明确证伪条件。不得替理论辩护。",
        ("critic", "foundations", "math", "physics", "complexity", "cosmology"), ("critic", "foundations"),
    ),
    "mathematical_modeling": WorkflowProfile(
        "mathematical_modeling", "数学建模", "Mathematical modeling",
        "先定义变量、单位、假设和边界条件，再给最小模型、推导步骤、可识别参数及失效条件。",
        ("math", "physics", "complexity", "critic"), ("math",),
    ),
    "experiment_design": WorkflowProfile(
        "experiment_design", "实验设计", "Experimental design",
        "输出可区分待检验假设与基线理论的预测、对照组、测量量、统计方法和预注册证伪标准。",
        ("physics", "math", "complexity", "critic"), ("math", "critic"),
    ),
    "open_exploration": WorkflowProfile(
        "open_exploration", "开放探索", "Open exploration",
        "探索潜在联系，但把类比与机制等价分开；寻找新假设同时主动记录反例和证据限制。",
        ("physics", "math", "complexity", "cosmology", "foundations", "critic"), ("critic",),
    ),
    "peer_review": WorkflowProfile(
        "peer_review", "同行评审", "Peer review",
        "按同行评审标准检查新颖性、方法、证据、统计、可复现性和已有理论覆盖范围。",
        ("critic", "foundations", "math", "physics", "complexity", "cosmology"), ("critic", "foundations"),
    ),
}


DEPTH_BUDGETS: Dict[str, Dict[str, int]] = {
    "fast": {"max_active_agents": 3, "max_parallel_agents": 1, "max_debate_rounds": 1, "max_context_tokens": 4096},
    "normal": {"max_active_agents": 7, "max_parallel_agents": 2, "max_debate_rounds": 2, "max_context_tokens": 8192},
    "deep": {"max_active_agents": 15, "max_parallel_agents": 2, "max_debate_rounds": 3, "max_context_tokens": 12288},
}
