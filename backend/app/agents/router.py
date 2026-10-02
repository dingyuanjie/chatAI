"""Transparent, bounded first-pass routing for research questions."""

from dataclasses import dataclass
from typing import List, Optional, Sequence

from .registry import AgentRegistry
from .workflows import DEPTH_BUDGETS, WORKFLOWS


@dataclass(frozen=True)
class RoutePlan:
    agent_ids: List[str]
    workflow_mode: str
    research_depth: str
    route_reason: str
    budget: dict


class AgentRouter:
    """Routes by explicit workflow plus auditable keyword/domain rules."""

    DOMAIN_RULES = (
        (("宇宙", "宇宙膨胀", "星系", "暗能量", "cosmolog", "galaxy", "universe"), ("cosmology", "physics"), "宇宙学/天体物理"),
        (("transformer", "人工智能", "大模型", "attention", "ai架构", "信息螺旋", "information spiral"), ("complexity", "math", "foundations"), "信息/AI/复杂系统"),
        (("实验", "观测", "测量", "experiment", "falsif", "证伪", "预测"), ("physics", "math"), "实验/预测"),
        (("数学", "公式", "方程", "模型", "变量", "mathemat", "equation", "model"), ("math", "physics"), "数学建模/形式化"),
        (("生命", "进化", "生态", "神经", "涌现", "复杂系统", "evolution", "life"), ("complexity", "foundations"), "生命/复杂系统"),
        (("结构生力", "svf", "结构", "生力", "螺旋", "理论", "theory", "structure"), ("foundations", "physics", "math"), "结构生力/理论基础"),
    )

    def __init__(self, registry: AgentRegistry):
        self.registry = registry

    def route(
        self,
        question: str,
        workflow_mode: str = "multidisciplinary",
        research_depth: str = "normal",
        requested_agents: Optional[Sequence[str]] = None,
    ) -> RoutePlan:
        if workflow_mode not in WORKFLOWS:
            raise ValueError(f"Unsupported workflow mode: {workflow_mode}")
        if research_depth not in DEPTH_BUDGETS:
            raise ValueError(f"Unsupported research depth: {research_depth}")
        budget = DEPTH_BUDGETS[research_depth]
        max_agents = budget["max_active_agents"]
        selectable = {agent.id for agent in self.registry.list_agents()}
        requested = list(dict.fromkeys(requested_agents or []))
        unknown = [agent for agent in requested if agent not in selectable]
        if unknown:
            raise ValueError(f"Unknown or non-selectable agent id: {', '.join(unknown)}")
        if len(requested) > max_agents:
            raise ValueError(f"Selected {len(requested)} agents, but {research_depth} depth allows at most {max_agents}")

        workflow = WORKFLOWS[workflow_mode]
        if requested:
            chosen = requested
            reason = "用户显式选择的专家角色"
        else:
            text = question.casefold()
            candidates: List[str] = []
            matched_domains = []
            for keywords, agent_ids, label in self.DOMAIN_RULES:
                if any(keyword.casefold() in text for keyword in keywords):
                    matched_domains.append(label)
                    candidates.extend(agent_ids)
                    # one domain match is usually sufficient; unrelated matches
                    # can still be added when the prompt clearly spans domains.
                    if len(matched_domains) >= 2:
                        break
            domain_agents = list(dict.fromkeys(candidates))
            if domain_agents:
                candidates = [*domain_agents, *workflow.initial_agents]
            else:
                candidates = list(workflow.initial_agents)
            candidates = [agent for agent in dict.fromkeys(candidates) if agent in selectable]
            target_count = min(max_agents, 3 if research_depth == "fast" else 5 if research_depth == "normal" else 8)
            if workflow_mode == "expert_consultation":
                target_count = min(target_count, 3)
            chosen = []
            for agent in (*workflow.required_agents, *candidates):
                if agent in selectable and agent not in chosen and len(chosen) < target_count:
                    chosen.append(agent)
            if len(chosen) < min(2, max_agents):
                for agent in ("physics", "math", "critic"):
                    if agent in selectable and agent not in chosen:
                        chosen.append(agent)
                    if len(chosen) >= min(2, max_agents):
                        break
            domain_text = "、".join(matched_domains) if matched_domains else "通用跨学科分析"
            reason = f"工作流“{workflow.name}”结合{domain_text}规则选角"

        if len(chosen) < 2:
            raise ValueError("At least two selectable agents are required for a research run")
        return RoutePlan(chosen, workflow_mode, research_depth, reason, dict(budget))
