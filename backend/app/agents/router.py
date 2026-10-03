"""可解释且有数量上限的科研角色路由：优先遵循用户显式选择，再结合研究工作流、问题关键词和研究深度自动选取专家。"""
# 中文模块说明：科研智能体配置与路由模块，负责专家角色定义、配置加载校验、领域匹配和研究工作流约束。

from dataclasses import dataclass
from typing import List, Optional, Sequence

from .registry import AgentRegistry
from .workflows import DEPTH_BUDGETS, WORKFLOWS


@dataclass(frozen=True)
class RoutePlan:
    """路由规划结果：包含专家 ID、工作流、档位、选角原因和并行预算。"""
    agent_ids: List[str]
    workflow_mode: str
    research_depth: str
    route_reason: str
    budget: dict


class AgentRouter:
    """按照明确的研究工作流和可审计的领域关键词规则规划专家列表，不在路由阶段调用大模型。"""

    DOMAIN_RULES = (
        (("广义相对论", "时空曲率", "引力波", "general relativity", "spacetime curvature", "gravitational wave"), ("relativity", "physics", "cosmology"), "相对论/引力"),
        (("粒子物理", "标准模型", "希格斯", "规范对称性", "particle physics", "standard model", "higgs"), ("particle_physics", "quantum", "physics"), "粒子物理"),
        (("凝聚态", "超导", "相变", "拓扑物态", "condensed matter", "superconduct", "phase transition"), ("condensed_matter", "quantum", "thermodynamics"), "凝聚态/相变"),
        (("天文观测", "恒星", "星系观测", "望远镜", "astronomical observation", "stellar", "telescope"), ("astronomy", "cosmology", "relativity"), "观测天文学"),
        (("湍流", "流体力学", "等离子体", "磁流体", "turbulence", "fluid dynamics", "plasma"), ("fluid_dynamics", "dynamical_systems", "thermodynamics"), "流体/等离子体"),
        (("地质", "地球科学", "行星宜居", "板块构造", "geoscience", "geology", "planetary habitability"), ("earth_science", "astronomy", "ecology"), "地球/行星科学"),
        (("化学反应", "催化", "分子自组装", "反应网络", "chemical reaction", "catalysis", "molecular self-assembly"), ("chemistry", "thermodynamics", "molecular_biology"), "化学/分子科学"),
        (("细胞", "基因调控", "细胞信号", "分子生物学", "cell biology", "gene regulation", "molecular biology"), ("molecular_biology", "systems_biology", "chemistry"), "分子/细胞生物学"),
        (("生态", "气候系统", "生物多样性", "生态韧性", "ecology", "climate system", "biodiversity"), ("ecology", "earth_science", "network_science"), "生态/气候系统"),
        (("系统生物学", "代谢网络", "生理稳态", "systems biology", "metabolic network", "homeostasis"), ("systems_biology", "molecular_biology", "network_science"), "系统生物学"),
        (("复杂网络", "网络科学", "网络动力学", "图网络", "network science", "network dynamics", "graph network"), ("network_science", "complexity", "math"), "网络科学"),
        (("非线性动力", "分岔", "混沌", "吸引子", "nonlinear dynamics", "bifurcation", "attractor"), ("dynamical_systems", "math", "complexity"), "非线性动力系统"),
        (("控制理论", "系统辨识", "反馈控制", "可观测性", "control theory", "system identification", "feedback control"), ("control_theory", "dynamical_systems", "math"), "控制/系统工程"),
        (("概率论", "随机过程", "大数定律", "概率模型", "probability theory", "stochastic process", "law of large numbers"), ("probability", "statistics", "math"), "概率/随机过程"),
        (("统计推断", "贝叶斯", "统计功效", "多重比较", "statistical inference", "bayesian", "statistical power"), ("statistics", "probability", "experimental_methods"), "统计推断"),
        (("因果推断", "因果图", "反事实", "混杂因素", "causal inference", "causal graph", "counterfactual"), ("causal_inference", "statistics", "experimental_methods"), "因果推断"),
        (("形式逻辑", "循环论证", "逻辑矛盾", "形式论证", "formal logic", "circular reasoning", "logical consistency"), ("logic", "foundations", "critic"), "逻辑/形式论证"),
        (("拓扑学", "微分几何", "流形", "拓扑不变量", "topology", "differential geometry", "manifold"), ("topology", "math", "relativity"), "拓扑/几何"),
        (("范畴论", "函子", "态射", "结构保持映射", "category theory", "functor", "morphism"), ("category_theory", "math", "foundations"), "范畴/结构数学"),
        (("科学哲学", "科学解释", "可证伪性", "理论实在论", "philosophy of science", "scientific explanation", "falsifiability"), ("philosophy_science", "foundations", "logic"), "科学哲学"),
        (("科学史", "概念史", "范式演化", "理论史", "history of science", "history of concepts", "paradigm shift"), ("history_science", "philosophy_science", "foundations"), "科学史/理论比较"),
        (("实验设计", "重复性", "预注册", "对照实验", "experimental design", "replication", "preregistration"), ("experimental_methods", "statistics", "critic"), "实验方法"),
        (("计量学", "误差预算", "仪器校准", "测量不确定度", "metrology", "uncertainty budget", "instrument calibration"), ("metrology", "experimental_methods", "statistics"), "计量/测量"),
        (("科学数据", "计算复现", "数据泄漏", "数据偏差", "scientific data", "computational reproducibility", "data leakage"), ("data_science", "statistics", "computation"), "科学数据/复现"),
        (("机器人", "具身智能", "主动感知", "传感器运动", "robotics", "embodied intelligence", "active perception"), ("robotics", "computation", "control_theory"), "机器人/具身智能"),
        (("语言学", "语言演化", "语义学", "心理语言学", "linguistics", "language evolution", "semantics"), ("linguistics", "neuroscience", "computation"), "语言/认知"),
        (("人类学", "文化演化", "考古", "跨文化", "anthropology", "cultural evolution", "archaeology"), ("anthropology", "history_science", "biology"), "人类学/文化演化"),
        (("博弈论", "机制设计", "经济系统", "合作演化", "game theory", "mechanism design", "economic system"), ("economics", "statistics", "network_science"), "博弈/经济系统"),
        (("量子", "纠缠", "测量问题", "quantum", "entanglement"), ("quantum", "physics", "math"), "量子理论/量子信息"),
        (("熵", "热力学", "非平衡", "耗散结构", "entropy", "thermodynamic", "nonequilibrium"), ("thermodynamics", "complexity", "physics"), "热力学/统计物理"),
        (("信息论", "互信息", "香农", "算法信息", "landauer", "information theory", "mutual information"), ("information", "math", "physics"), "信息论/计算"),
        (("生命起源", "生物", "遗传", "演化", "进化", "生态", "origin of life", "biology", "evolution"), ("biology", "thermodynamics", "complexity"), "生命科学/演化"),
        (("神经科学", "意识", "认知", "大脑", "neuroscience", "consciousness", "cognition"), ("neuroscience", "biology", "information"), "认知/神经科学"),
        (("人工智能", "大模型", "transformer", "机器学习", "attention", "ai architecture", "machine learning"), ("computation", "information", "complexity"), "计算/人工智能"),
        (("宇宙", "宇宙膨胀", "星系", "暗能量", "cosmolog", "galaxy", "universe"), ("cosmology", "physics"), "宇宙学/天体物理"),
        (("transformer", "人工智能", "大模型", "attention", "ai架构", "信息螺旋", "information spiral"), ("complexity", "math", "foundations"), "信息/AI/复杂系统"),
        (("实验", "观测", "测量", "experiment", "falsif", "证伪", "预测"), ("physics", "math"), "实验/预测"),
        (("数学", "公式", "方程", "模型", "变量", "mathemat", "equation", "model"), ("math", "physics"), "数学建模/形式化"),
        (("生命", "进化", "生态", "神经", "涌现", "复杂系统", "evolution", "life"), ("complexity", "foundations"), "生命/复杂系统"),
        (("结构生力", "svf", "结构", "生力", "螺旋", "理论", "theory", "structure"), ("foundations", "physics", "math"), "结构生力/理论基础"),
    )

    def __init__(self, registry: AgentRegistry):
        """保存配置目录；所有最终角色必须来自该注册表中的可选专家。"""
        self.registry = registry

    def route(
        self,
        question: str,
        workflow_mode: str = "multidisciplinary",
        research_depth: str = "normal",
        requested_agents: Optional[Sequence[str]] = None,
    ) -> RoutePlan:
        """根据显式角色或问题命中领域规则形成有上限的专家列表。

        用户手动选择时不偷偷增删角色；自动路由时先取最多两个领域命中，再结合工作流
        必需角色补齐。最终至少两个角色，并强制遵守对应研究深度的 active-agent 上限。
        """
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
                    # 通常一个领域命中已足够；如果问题明显跨学科，最多再合并第二个领域规则。
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
