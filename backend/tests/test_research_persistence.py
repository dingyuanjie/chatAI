# 中文模块说明：验证科研任务/输出/图谱的持久化、所有者隔离、模型切换记录、记忆恢复及调用预算。
import tempfile
import unittest
import sqlite3
from pathlib import Path
from unittest.mock import patch

from app import research
from app.research import CreateResearchRun, ResearchEngine
from app.providers.models import ModelProviderError, ModelUsage


class ResearchPersistenceTests(unittest.TestCase):
    """覆盖科研任务的数据库生命周期、用户隔离、证据图谱、记忆、恢复和资源记录。"""
    def setUp(self):
        """为每个测试创建临时研究数据库和独立科研引擎。"""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = research.RESEARCH_DB
        research.RESEARCH_DB = Path(self.temp_dir.name) / "research.sqlite"
        self.engine = ResearchEngine(rag_store=object())

    def tearDown(self):
        """恢复原数据库路径并删除测试产生的临时文件。"""
        research.RESEARCH_DB = self.db_path
        self.temp_dir.cleanup()

    def test_run_and_output_are_persisted_and_owner_scoped(self):
        """确认任务和输出会持久化，并且只有创建者能够读取任务详情。"""
        payload = CreateResearchRun(
            title="结构问题",
            question="结构生力有哪些可检验定义？",
            agents=["math", "critic"],
            max_rounds=3,
        )
        with patch.object(self.engine, "_start"):
            run = self.engine.create(payload, "owner-a")

        self.engine._save_output(run["id"], 1, "math", "明确变量与假设。", [], "completed")
        detail = self.engine.get_detail(run["id"], "owner-a")

        self.assertEqual(detail["current_round"], 0)
        self.assertEqual(detail["outputs"][0]["agent_id"], "math")
        self.assertEqual(detail["outputs"][0]["content"], "明确变量与假设。")
        self.assertEqual(detail["tasks"][0]["stage"], "expert_analysis")
        self.assertEqual(detail["tasks"][0]["status"], "completed")
        with self.assertRaises(research.HTTPException) as error:
            self.engine.get_detail(run["id"], "owner-b")
        self.assertEqual(error.exception.status_code, 404)

    def test_model_switch_usage_is_logged_without_resetting_research_memory(self):
        """确认同一研究切换本地/远端模型时分别记录用量，同时保留已有研究记忆。"""
        payload = CreateResearchRun(title="Provider 切换", question="检查切换模型后记忆是否保留。", agents=["math", "critic"], max_rounds=0)
        with patch.object(self.engine, "_start"):
            run = self.engine.create(payload, "owner-a")
        self.engine._save_output(run["id"], 1, "math", "[finding] 有一项发现需要持续跟踪。", [], "completed")
        self.engine._save_memory(run["id"], 1, "保留的研究摘要")
        self.engine._record_model_call(run["id"], 1, "math", "agent", "ollama", "LOCAL", "qwen-local", True,
            usage=ModelUsage(input_tokens=100, output_tokens=30, total_tokens=130))
        self.engine._record_model_call(run["id"], 2, "critic", "agent", "api.deepseek.com", "REMOTE", "deepseek-flash", True,
            usage=ModelUsage(input_tokens=50, output_tokens=25, total_tokens=75, estimated_cost=0.0001, currency="USD"))
        detail = self.engine.get_detail(run["id"], "owner-a")
        self.assertEqual(len(detail["memory"]), 1)
        self.assertEqual(detail["resource_usage"]["model_calls"], 2)
        self.assertEqual(detail["resource_usage"]["total_tokens"], 205)
        self.assertEqual({item["provider_type"] for item in detail["resource_usage"]["model_breakdown"]}, {"LOCAL", "REMOTE"})

    def test_model_call_budget_is_reserved_before_parallel_requests(self):
        """确认并行调用必须先原子预留额度，预算最后一个名额不能被两个请求同时使用。"""
        payload = CreateResearchRun(title="调用预算", question="检查并发请求不会突破调用预算。", agents=["math", "critic"],
            max_rounds=0, continuous_config={"preset": "balanced", "max_model_calls": 1})
        with patch.object(self.engine, "_start"):
            run = self.engine.create(payload, "owner-a")
        provider_type = run["provider_type"]
        first = ResearchEngine._reserve_model_call(run["id"], 1, "math", "agent", "ollama", provider_type, "test-model")
        self.assertIsNotNone(first)
        with self.assertRaises(ModelProviderError) as error:
            ResearchEngine._reserve_model_call(run["id"], 1, "critic", "agent", "ollama", provider_type, "test-model")
        self.assertEqual(error.exception.reason_code, "CALL_BUDGET_EXHAUSTED")
        detail = self.engine.get_detail(run["id"], "owner-a")
        self.assertEqual(detail["resource_usage"]["model_calls"], 1)
        self.assertEqual(detail["resource_usage"]["successful_calls"], 0)
        self.assertEqual(detail["resource_usage"]["failed_calls"], 0)
        self.assertEqual(detail["resource_usage"]["model_breakdown"][0]["pending_calls"], 1)

    def test_runtime_usage_survives_pause_and_resume(self):
        """确认累计运行秒数在暂停和恢复后保留，并继续出现在资源统计中。"""
        with patch.object(self.engine, "_start"):
            run = self.engine.create(CreateResearchRun(title="累计运行时长", question="检查续跑资源统计。", agents=["math", "critic"],
                max_rounds=0), "owner-a")
            ResearchEngine._set_status(run["id"], "paused")
            with research.connection() as conn:
                conn.execute("UPDATE research_runs SET runtime_seconds_total=125 WHERE id=?", (run["id"],))
            resumed = self.engine.control(run["id"], "owner-a", "resume")
        self.assertEqual(resumed["status"], "running")
        detail = self.engine.get_detail(run["id"], "owner-a")
        self.assertGreaterEqual(detail["resource_usage"]["runtime_seconds"], 125)

    def test_output_evidence_is_normalized_and_linked_to_agent_task(self):
        """确认输出引用会转换为证据记录，结构化主张可与证据建立支持/反驳关系。"""
        payload = CreateResearchRun(
            title="来源追踪",
            question="检查资料来源如何链接到研究结论？",
            agents=["math", "critic"],
            max_rounds=1,
        )
        with patch.object(self.engine, "_start"):
            run = self.engine.create(payload, "owner-a")

        self.engine._save_output(run["id"], 1, "math", "- [external] 该研究报告了可供比较的观测结果 [W1]\n- [hypothesis] 结构生力机制可能产生额外的可测偏差。\n- [counterevidence] 现有观测可能与该假设不一致 [W1]", [{
            "provider": "Crossref", "title": "A published paper", "url": "https://doi.org/10.1/example", "snippet": "Abstract excerpt", "citation_label": "W1"
        }])
        detail = self.engine.get_detail(run["id"], "owner-a")

        self.assertEqual(len(detail["evidence"]), 1)
        self.assertEqual(detail["evidence"][0]["source_type"], "bibliographic_record")
        self.assertEqual(detail["evidence"][0]["cited_by"], ["math"])
        self.assertEqual(len(detail["claims"]), 3)
        external_claim = next(claim for claim in detail["claims"] if claim["claim_type"] == "finding")
        counter_claim = next(claim for claim in detail["claims"] if claim["claim_type"] == "counterevidence")
        self.assertEqual(external_claim["epistemic_status"], "external_source")
        self.assertEqual(external_claim["evidence"][0]["relation"], "supports")
        self.assertEqual(counter_claim["evidence"][0]["relation"], "contradicts")
        graph = self.engine.get_graph(run["id"], "owner-a")
        graph_types = {node["type"] for node in graph["nodes"]}
        graph_relations = {edge["relation"] for edge in graph["edges"]}
        self.assertTrue({"task", "output", "claim", "evidence"}.issubset(graph_types))
        self.assertIn("supports", graph_relations)
        self.assertIn("contradicts", graph_relations)
        with self.assertRaises(research.HTTPException) as graph_error:
            self.engine.get_graph(run["id"], "owner-b")
        self.assertEqual(graph_error.exception.status_code, 404)

    def test_cross_run_memory_is_retrieved_by_relevance_and_owner_scoped(self):
        """确认历史研究记忆按问题相关性复用，并严格限制在相同用户范围。"""
        with patch.object(self.engine, "_start"):
            prior = self.engine.create(CreateResearchRun(
                title="结构生力的可证伪预测",
                question="结构生力理论能否解释宇宙结构形成？",
                agents=["physics", "critic"],
                max_rounds=1,
            ), "owner-a")
            same_owner = self.engine.create(CreateResearchRun(
                title="宇宙结构的后续研究",
                question="结构生力理论是否能够解释宇宙结构形成和尺度涌现？",
                agents=["physics", "critic"],
                max_rounds=1,
            ), "owner-a")
            other_owner = self.engine.create(CreateResearchRun(
                title="宇宙结构",
                question="结构生力理论是否能够解释宇宙结构形成？",
                agents=["physics", "critic"],
                max_rounds=1,
            ), "owner-b")

        self.engine._save_output(prior["id"], 1, "physics", "- [hypothesis] 结构生力可能预测宇宙结构存在特定尺度关系。", [])
        self.engine._save_output(prior["id"], 1, "synthesis", "初步记录：需要与标准宇宙学比较。", [])
        self.engine._save_memory(prior["id"], 1, "初步记录：需要与标准宇宙学比较。")

        selected = self.engine._retrieve_memories("owner-a", same_owner["id"], 1, same_owner["question"])
        not_shared = self.engine._retrieve_memories("owner-b", other_owner["id"], 1, other_owner["question"])
        detail = self.engine.get_detail(same_owner["id"], "owner-a")

        self.assertEqual([item["run_id"] for item in selected], [prior["id"]])
        self.assertEqual(not_shared, [])
        self.assertEqual(detail["related_memory"][0]["id"], selected[0]["id"])
        self.assertEqual(len(detail["memory"]), 0)

    def test_internal_debate_output_is_saved_as_a_debate_stage(self):
        """确认交叉质疑输出保存为独立评审阶段并能显示正确专家名称。"""
        payload = CreateResearchRun(
            title="理论评审",
            question="请攻击结构生力理论的核心主张。",
            agents=["critic", "foundations"],
            workflow_mode="theory_attack",
            max_rounds=1,
        )
        with patch.object(self.engine, "_start"):
            run = self.engine.create(payload, "owner-a")

        self.engine._save_output(run["id"], 1, "debate_critic", "提出反例和证伪标准。", [], "completed")
        detail = self.engine.get_detail(run["id"], "owner-a")

        self.assertEqual(detail["tasks"][0]["stage"], "debate")
        self.assertEqual(detail["outputs"][0]["agent_name"], "交叉质疑评审员")

    def test_theory_attack_runs_critic_before_synthesis_and_completes(self):
        """确认理论攻击工作流先执行批判评审与研究员回应，再生成综合结果。"""
        payload = CreateResearchRun(
            title="理论攻击测试",
            question="请攻击结构生力理论的核心主张。",
            agents=["critic", "foundations"],
            workflow_mode="theory_attack",
            max_rounds=1,
        )
        with patch.object(self.engine, "_start"):
            run = self.engine.create(payload, "owner-a")

        prompts = []
        def answer(system, prompt, temperature=0.35, max_tokens=2048, **kwargs):
            """验证科研输出、任务阶段和运行状态写入 SQLite 后，可以通过详情接口完整读回。"""
            prompts.append(prompt)
            return f"完成：{system[:20]}"

        with patch("app.research.online_science_search", return_value=[]), patch.object(self.engine, "_call_model", side_effect=answer):
            self.engine._work(run["id"])

        detail = self.engine.get_detail(run["id"], "owner-a")
        output_ids = {output["agent_id"] for output in detail["outputs"]}
        self.assertEqual(detail["status"], "completed")
        self.assertIn("debate_critic", output_ids)
        self.assertIn("debate_response", output_ids)
        self.assertIn("synthesis", output_ids)
        self.assertTrue(any("交叉质疑" in prompt for prompt in prompts))
        self.assertTrue(any("交叉评审意见" in prompt for prompt in prompts))
        self.assertEqual({task["status"] for task in detail["tasks"]}, {"completed"})
        self.assertEqual(len(detail["memory"]), 1)

    def test_legacy_database_gets_stage_and_evidence_tables(self):
        """确认旧版数据库在初始化时可迁移到任务阶段、证据和主张关联表。"""
        active_db = research.RESEARCH_DB
        legacy_dir = tempfile.TemporaryDirectory()
        legacy_db = Path(legacy_dir.name) / "legacy.sqlite"
        try:
            research.RESEARCH_DB = legacy_db
            conn = sqlite3.connect(legacy_db)
            try:
                conn.execute("""CREATE TABLE research_runs (
                    id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, title TEXT NOT NULL, question TEXT NOT NULL,
                    agents_json TEXT NOT NULL, max_rounds INTEGER NOT NULL, current_round INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL, summary TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                )""")
                conn.execute("""CREATE TABLE research_outputs (
                    id TEXT PRIMARY KEY, run_id TEXT NOT NULL, round_no INTEGER NOT NULL, agent_id TEXT NOT NULL,
                    agent_name TEXT NOT NULL, status TEXT NOT NULL, content TEXT NOT NULL,
                    sources_json TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL,
                    UNIQUE(run_id, round_no, agent_id)
                )""")
            finally:
                conn.close()

            ResearchEngine(rag_store=object())
            conn = sqlite3.connect(legacy_db)
            try:
                run_columns = {row[1] for row in conn.execute("PRAGMA table_info(research_runs)")}
                tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                evidence_columns = {row[1] for row in conn.execute("PRAGMA table_info(research_evidence)")}
            finally:
                conn.close()
            self.assertIn("current_stage", run_columns)
            self.assertIn("research_tasks", tables)
            self.assertIn("research_evidence", tables)
            self.assertIn("research_claims", tables)
            self.assertIn("research_claim_evidence", tables)
            self.assertIn("citation_label", evidence_columns)
        finally:
            research.RESEARCH_DB = active_db
            legacy_dir.cleanup()

    def test_failed_run_resumes_from_saved_checkpoint(self):
        """确认失败任务恢复时从已保存检查点继续，而不是重新创建任务。"""
        payload = CreateResearchRun(
            title="持续研究",
            question="宇宙学结构与观测如何比较？",
            agents=["cosmology", "critic"],
            max_rounds=0,
        )
        with patch.object(self.engine, "_start"):
            run = self.engine.create(payload, "owner-a")
            self.engine._save_output(run["id"], 4, "synthesis", "综合失败", [], "failed")
            with research.connection() as conn:
                conn.execute("UPDATE research_runs SET current_round=4 WHERE id=?", (run["id"],))
            ResearchEngine._set_status(run["id"], "failed")
            resumed = self.engine.control(run["id"], "owner-a", "resume")

        self.assertEqual(resumed["status"], "running")
        self.assertTrue(resumed["continuous"])
        self.assertEqual(resumed["current_round"], 3)

    def test_non_registered_and_internal_agents_cannot_be_selected(self):
        """确认未知 ID 与仅内部角色不能通过用户请求进入专家列表。"""
        for selected in (["physics", "not-registered"], ["physics", "synthesis"]):
            payload = CreateResearchRun(
                title="验证",
                question="检查角色注册表边界行为。",
                agents=selected,
                max_rounds=1,
            )
            with self.assertRaises(research.HTTPException) as error:
                self.engine.create(payload, "owner-a")
            self.assertEqual(error.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
