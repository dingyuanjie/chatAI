# 中文模块说明：验证领域关键词路由、工作流必需角色、研究深度预算和用户显式选择。
import unittest

from app.agents.registry import AgentRegistry
from app.agents.router import AgentRouter
from app.agents.workflows import DEPTH_BUDGETS, WORKFLOWS


class AgentRouterTests(unittest.TestCase):
    """验证研究问题、工作流、档位和用户选择如何组合成专家路由计划。"""
    def setUp(self):
        """加载真实专家目录并构造路由器，后续测试针对真实档案验证角色选择。"""
        from pathlib import Path
        profiles = Path(__file__).parents[1] / "app" / "agents" / "profiles"
        self.router = AgentRouter(AgentRegistry.from_directory(profiles))

    def test_routes_svf_question_to_small_foundations_group(self):
        """验证结构生力问题被路由到理论基础及相关小组，而不会无故启用全部专家。"""
        plan = self.router.route("结构生力是什么？", "expert_consultation", "fast")

        self.assertLessEqual(len(plan.agent_ids), 3)
        self.assertIn("foundations", plan.agent_ids)

    def test_routes_information_spiral_transformer_to_ai_adjacent_roles_and_critic(self):
        """验证信息螺旋与 Transformer 问题能选中 AI/信息相关专家和批判审查角色。"""
        plan = self.router.route("信息螺旋是否可以用于 Transformer？")

        self.assertIn("complexity", plan.agent_ids)
        self.assertIn("math", plan.agent_ids)
        self.assertIn("critic", plan.agent_ids)
        self.assertLessEqual(len(plan.agent_ids), DEPTH_BUDGETS["normal"]["max_active_agents"])

    def test_routes_cosmic_expansion_to_cosmology_and_critical_review(self):
        """验证宇宙膨胀问题被分配给宇宙学专家，并包含必要的审查视角。"""
        plan = self.router.route("结构生力能否解释宇宙膨胀？")

        self.assertIn("cosmology", plan.agent_ids)
        self.assertIn("physics", plan.agent_ids)
        self.assertIn("critic", plan.agent_ids)

    def test_routes_quantum_and_entropy_questions_to_new_specialists(self):
        """验证量子与熵问题能匹配新增的量子/热力学领域专家。"""
        quantum = self.router.route("量子纠缠是否支持结构生力理论？", research_depth="deep")
        entropy = self.router.route("熵增与非平衡耗散结构如何导致层级涌现？", research_depth="deep")

        self.assertIn("quantum", quantum.agent_ids)
        self.assertIn("thermodynamics", entropy.agent_ids)

    def test_routes_life_information_and_ai_questions_to_specialists(self):
        """验证生命、信息与人工智能的跨学科问题能结合多个匹配领域。"""
        life = self.router.route("生命起源与演化能否由统一结构解释？", research_depth="deep")
        information = self.router.route("香农信息和物理熵有什么可检验关系？", research_depth="deep")
        ai = self.router.route("Transformer 大模型是否涌现出新的计算机制？", research_depth="deep")

        self.assertIn("biology", life.agent_ids)
        self.assertIn("information", information.agent_ids)
        self.assertIn("computation", ai.agent_ids)

    def test_new_catalogue_specialists_are_auto_routable(self):
        """确认新增专家目录中的专业领域可被自动路由规则发现。"""
        fixtures = {
            "relativity": "gravitational wave spacetime curvature",
            "particle_physics": "particle physics standard model",
            "condensed_matter": "superconducting phase transition",
            "astronomy": "telescope stellar observation",
            "fluid_dynamics": "plasma turbulence",
            "earth_science": "geology planetary habitability",
            "chemistry": "catalysis chemical reaction",
            "molecular_biology": "cell biology gene regulation",
            "ecology": "climate system biodiversity",
            "systems_biology": "systems biology metabolic network",
            "network_science": "network dynamics graph network",
            "dynamical_systems": "nonlinear dynamics bifurcation",
            "control_theory": "control theory system identification",
            "probability": "stochastic process probability theory",
            "statistics": "Bayesian statistical inference",
            "causal_inference": "causal graph counterfactual",
            "logic": "formal logic logical consistency",
            "topology": "topology differential geometry manifold",
            "category_theory": "category theory functor morphism",
            "philosophy_science": "philosophy of science scientific explanation",
            "history_science": "history of science history of concepts",
            "experimental_methods": "experimental design preregistration replication",
            "metrology": "metrology uncertainty budget calibration",
            "data_science": "computational reproducibility data leakage",
            "robotics": "robotics embodied intelligence active perception",
            "linguistics": "linguistics semantics language evolution",
            "anthropology": "anthropology cultural evolution archaeology",
            "economics": "game theory mechanism design",
        }
        for expected_agent, question in fixtures.items():
            with self.subTest(agent=expected_agent):
                plan = self.router.route(question, research_depth="deep")
                self.assertIn(expected_agent, plan.agent_ids)

    def test_theory_attack_requires_critic_and_foundations(self):
        """确认理论攻击工作流必须包含批判者与理论基础角色。"""
        plan = self.router.route("请攻击结构生力理论。", "theory_attack", "deep")

        self.assertIn("critic", plan.agent_ids)
        self.assertIn("foundations", plan.agent_ids)
        self.assertLessEqual(len(plan.agent_ids), DEPTH_BUDGETS["deep"]["max_active_agents"])

    def test_experiment_design_includes_methods_and_falsification_roles(self):
        """确认实验设计会包含方法学和证伪审查角色。"""
        plan = self.router.route("请设计一个能够证伪信息螺旋的实验。", "experiment_design", "normal")

        self.assertIn("math", plan.agent_ids)
        self.assertIn("critic", plan.agent_ids)
        self.assertIn("证伪标准", WORKFLOWS[plan.workflow_mode].instruction)

    def test_explicit_agent_selection_is_preserved_and_budgeted(self):
        """确认用户显式选择的角色不被自动替换，并受当前研究档位人数上限约束。"""
        plan = self.router.route("一个一般问题", requested_agents=["cosmology", "critic"])

        self.assertEqual(plan.agent_ids, ["cosmology", "critic"])
        with self.assertRaisesRegex(ValueError, "Unknown or non-selectable"):
            self.router.route("一般问题", requested_agents=["synthesis", "critic"])

    def test_unknown_mode_and_depth_are_rejected(self):
        """确认未注册的工作流模式或研究深度会被明确拒绝。"""
        with self.assertRaisesRegex(ValueError, "Unsupported workflow"):
            self.router.route("一个问题", "unknown")
        with self.assertRaisesRegex(ValueError, "Unsupported research depth"):
            self.router.route("一个问题", research_depth="unlimited")


if __name__ == "__main__":
    unittest.main()
