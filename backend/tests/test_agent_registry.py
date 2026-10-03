# 中文模块说明：验证专家 JSON 档案的加载、字段校验、唯一 ID、目录查询和内部智能体过滤。
import json
import tempfile
import unittest
from pathlib import Path

from app.agents.base import AgentProfile
from app.agents.registry import AgentRegistry


PROFILES = Path(__file__).parents[1] / "app" / "agents" / "profiles"


class AgentRegistryTests(unittest.TestCase):
    """覆盖专家配置加载与注册表查询规则的单元测试。"""
    def test_loads_existing_experts_and_keeps_synthesis_internal(self):
        """确认 profiles 目录中的专家能加载，并验证仅内部使用的综合角色不会出现在可选专家列表。"""
        registry = AgentRegistry.from_directory(PROFILES)
        added_ids = {
            "relativity", "particle_physics", "condensed_matter", "astronomy", "fluid_dynamics", "earth_science",
            "chemistry", "molecular_biology", "ecology", "systems_biology", "network_science", "dynamical_systems",
            "control_theory", "probability", "statistics", "causal_inference", "logic", "topology", "category_theory",
            "philosophy_science", "history_science", "experimental_methods", "metrology", "data_science", "robotics",
            "linguistics", "anthropology", "economics",
        }

        self.assertEqual(len(registry.list_agents()), 40)
        self.assertTrue(added_ids.issubset({agent.id for agent in registry.list_agents()}))
        self.assertEqual(len(registry.list_agents(include_internal=True)), 43)
        self.assertFalse(registry.get_agent("synthesis").selectable)

    def test_domain_and_capability_lookup_are_case_insensitive(self):
        """确认领域与技能匹配忽略大小写，避免 JSON 大小写差异影响自动路由。"""
        registry = AgentRegistry.from_directory(PROFILES)

        self.assertEqual([agent.id for agent in registry.find_agents_by_domain("COSMOLOGY")], ["cosmology"])
        self.assertIn("critic", {agent.id for agent in registry.find_agents_by_capability("FALSIFIABILITY")})

    def test_public_payload_keeps_legacy_frontend_fields(self):
        """确认公开专家数据保留现有前端字段，同时不泄露系统提示词等内部配置。"""
        payload = AgentRegistry.from_directory(PROFILES).get_agent("physics").to_public_dict()

        self.assertEqual(payload["id"], "physics")
        self.assertIn("name", payload)
        self.assertIn("name_en", payload)
        self.assertIn("focus", payload)
        self.assertIn("focus_en", payload)

    def test_duplicate_ids_are_rejected(self):
        """确认注册表拒绝重复的专家 ID，防止路由时出现不确定角色。"""
        profile_data = json.loads((PROFILES / "physics.json").read_text(encoding="utf-8"))
        profile = AgentProfile.from_dict(profile_data)
        registry = AgentRegistry([profile])

        with self.assertRaisesRegex(ValueError, "already registered"):
            registry.register_agent(profile)

    def test_malformed_profile_is_rejected(self):
        """确认缺失必填字段或生成参数越界的专家档案在加载时被拒绝。"""
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "broken.json").write_text('{"id":"broken"}', encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "missing required string fields"):
                AgentRegistry.from_directory(Path(directory))


if __name__ == "__main__":
    unittest.main()
