import tempfile
import unittest
import sqlite3
from pathlib import Path
from unittest.mock import patch

from app import research
from app.research import CreateResearchRun, ResearchEngine


class ResearchPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = research.RESEARCH_DB
        research.RESEARCH_DB = Path(self.temp_dir.name) / "research.sqlite"
        self.engine = ResearchEngine(rag_store=object())

    def tearDown(self):
        research.RESEARCH_DB = self.db_path
        self.temp_dir.cleanup()

    def test_run_and_output_are_persisted_and_owner_scoped(self):
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

    def test_output_evidence_is_normalized_and_linked_to_agent_task(self):
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
        def answer(system, prompt, temperature=0.35, max_tokens=2048):
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
