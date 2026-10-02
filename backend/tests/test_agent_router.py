import unittest

from app.agents.registry import AgentRegistry
from app.agents.router import AgentRouter
from app.agents.workflows import DEPTH_BUDGETS, WORKFLOWS


class AgentRouterTests(unittest.TestCase):
    def setUp(self):
        from pathlib import Path
        profiles = Path(__file__).parents[1] / "app" / "agents" / "profiles"
        self.router = AgentRouter(AgentRegistry.from_directory(profiles))

    def test_routes_svf_question_to_small_foundations_group(self):
        plan = self.router.route("结构生力是什么？", "expert_consultation", "fast")

        self.assertLessEqual(len(plan.agent_ids), 3)
        self.assertIn("foundations", plan.agent_ids)

    def test_routes_information_spiral_transformer_to_ai_adjacent_roles_and_critic(self):
        plan = self.router.route("信息螺旋是否可以用于 Transformer？")

        self.assertIn("complexity", plan.agent_ids)
        self.assertIn("math", plan.agent_ids)
        self.assertIn("critic", plan.agent_ids)
        self.assertLessEqual(len(plan.agent_ids), DEPTH_BUDGETS["normal"]["max_active_agents"])

    def test_routes_cosmic_expansion_to_cosmology_and_critical_review(self):
        plan = self.router.route("结构生力能否解释宇宙膨胀？")

        self.assertIn("cosmology", plan.agent_ids)
        self.assertIn("physics", plan.agent_ids)
        self.assertIn("critic", plan.agent_ids)

    def test_theory_attack_requires_critic_and_foundations(self):
        plan = self.router.route("请攻击结构生力理论。", "theory_attack", "deep")

        self.assertIn("critic", plan.agent_ids)
        self.assertIn("foundations", plan.agent_ids)
        self.assertLessEqual(len(plan.agent_ids), DEPTH_BUDGETS["deep"]["max_active_agents"])

    def test_experiment_design_includes_methods_and_falsification_roles(self):
        plan = self.router.route("请设计一个能够证伪信息螺旋的实验。", "experiment_design", "normal")

        self.assertIn("math", plan.agent_ids)
        self.assertIn("critic", plan.agent_ids)
        self.assertIn("证伪标准", WORKFLOWS[plan.workflow_mode].instruction)

    def test_explicit_agent_selection_is_preserved_and_budgeted(self):
        plan = self.router.route("一个一般问题", requested_agents=["cosmology", "critic"])

        self.assertEqual(plan.agent_ids, ["cosmology", "critic"])
        with self.assertRaisesRegex(ValueError, "Unknown or non-selectable"):
            self.router.route("一般问题", requested_agents=["synthesis", "critic"])

    def test_unknown_mode_and_depth_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "Unsupported workflow"):
            self.router.route("一个问题", "unknown")
        with self.assertRaisesRegex(ValueError, "Unsupported research depth"):
            self.router.route("一个问题", research_depth="unlimited")


if __name__ == "__main__":
    unittest.main()
