"""Durable multi-agent science exploration tasks."""

import json
import os
import re
import sqlite3
import threading
import time
import uuid
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import quote, urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from app.agents.registry import AgentRegistry
from app.agents.router import AgentRouter
from app.agents.workflows import DEPTH_BUDGETS, WORKFLOWS
from app.providers.openai_compatible import OpenAICompatibleChatModel
from app.providers.models import ModelProviderError
from app.providers.registry import model_provider_registry
from app.model_settings import model_settings
from app.retrieval.base import RAGStoreAdapter
from app.continuous_research.continuation_judge import ResearchContinuationJudge
from app.continuous_research.controller import ContinuousResearchController
from app.continuous_research.models import ContinuousResearchConfig
from app.continuous_research.loop_detector import ResearchLoopDetector
from app.continuous_research.novelty_detector import NoveltyDetector
from app.continuous_research.progress_tracker import ProgressTracker
from app.continuous_research.resource_usage import ResearchResourceUsage


DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
RESEARCH_DB = DATA_DIR / "research.sqlite"
AGENT_REGISTRY = AgentRegistry.from_directory(Path(__file__).parent / "agents" / "profiles")
AGENTS = {profile.id: profile for profile in AGENT_REGISTRY.list_agents(include_internal=True)}
AGENT_ROUTER = AgentRouter(AGENT_REGISTRY)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def connection():
    conn = sqlite3.connect(RESEARCH_DB.as_posix(), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def online_science_search(query: str, limit: int = 8) -> List[Dict[str, str]]:
    """Search Crossref and arXiv APIs for real scholarly material."""
    hits: List[Dict[str, str]] = []
    timeout = httpx.Timeout(20.0, connect=8.0)
    headers = {"User-Agent": "StructuralVitalForceResearch/1.0 (local research workspace)"}
    try:
        response = httpx.get(
            "https://api.crossref.org/works",
            params={"query.bibliographic": query, "rows": max(3, limit // 2), "select": "DOI,title,published,container-title,abstract,URL,author"},
            headers=headers,
            timeout=timeout,
        )
        response.raise_for_status()
        for item in response.json().get("message", {}).get("items", []):
            title = (item.get("title") or [""])[0].strip()
            url = item.get("URL") or (f"https://doi.org/{item['DOI']}" if item.get("DOI") else "")
            if title and url:
                abstract = item.get("abstract", "")
                hits.append({"provider": "Crossref", "title": title, "url": url, "snippet": " ".join(abstract.replace("<jats:p>", "").replace("</jats:p>", "").split())[:900]})
    except Exception:
        pass
    try:
        response = httpx.get(
            "https://export.arxiv.org/api/query",
            params={"search_query": f"all:{query}", "start": 0, "max_results": max(3, limit // 2)},
            headers=headers,
            timeout=timeout,
        )
        response.raise_for_status()
        root = ET.fromstring(response.text)
        ns = {"a": "http://www.w3.org/2005/Atom"}
        for entry in root.findall("a:entry", ns):
            title = " ".join((entry.findtext("a:title", default="", namespaces=ns)).split())
            url = entry.findtext("a:id", default="", namespaces=ns)
            abstract = " ".join((entry.findtext("a:summary", default="", namespaces=ns)).split())
            if title and url:
                hits.append({"provider": "arXiv", "title": title, "url": url, "snippet": abstract[:900]})
    except Exception:
        pass
    unique: List[Dict[str, str]] = []
    seen = set()
    for hit in hits:
        if hit["url"] not in seen:
            unique.append(hit)
            seen.add(hit["url"])
        if len(unique) >= limit:
            break
    return unique


class CreateResearchRun(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    question: str = Field(min_length=8, max_length=10000)
    agents: List[str] = Field(default_factory=list, max_length=15)
    max_rounds: int = Field(default=5, ge=0, le=100)
    workflow_mode: str = Field(default="multidisciplinary", min_length=1, max_length=64)
    research_depth: str = Field(default="normal", min_length=1, max_length=16)
    continuous_config: Dict[str, Any] = Field(default_factory=dict)


class EmergencyStopRequest(BaseModel):
    enabled: bool


class PreviewRouteRequest(BaseModel):
    question: str = Field(min_length=8, max_length=10000)
    workflow_mode: str = Field(default="multidisciplinary", min_length=1, max_length=64)
    research_depth: str = Field(default="normal", min_length=1, max_length=16)
    agents: List[str] = Field(default_factory=list, max_length=15)


class ResearchEngine:
    def __init__(self, rag_store: Any):
        self.retriever = RAGStoreAdapter(rag_store)
        self._threads: Dict[str, threading.Thread] = {}
        self._lock = threading.Lock()
        with connection() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
        with connection() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS research_runs (
                id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, title TEXT NOT NULL, question TEXT NOT NULL,
                agents_json TEXT NOT NULL, max_rounds INTEGER NOT NULL, current_round INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL, summary TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )""")
            conn.execute("""CREATE TABLE IF NOT EXISTS research_outputs (
                id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES research_runs(id) ON DELETE CASCADE,
                round_no INTEGER NOT NULL, agent_id TEXT NOT NULL, agent_name TEXT NOT NULL,
                status TEXT NOT NULL, content TEXT NOT NULL, sources_json TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL, UNIQUE(run_id, round_no, agent_id)
            )""")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_research_owner_updated ON research_runs(owner_id, updated_at DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_research_outputs_run ON research_outputs(run_id, round_no)")
            conn.execute("""CREATE TABLE IF NOT EXISTS research_tasks (
                id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES research_runs(id) ON DELETE CASCADE,
                round_no INTEGER NOT NULL, stage TEXT NOT NULL, agent_id TEXT NOT NULL,
                status TEXT NOT NULL, attempt INTEGER NOT NULL DEFAULT 1, output_id TEXT,
                error TEXT NOT NULL DEFAULT '', started_at TEXT NOT NULL, completed_at TEXT,
                UNIQUE(run_id, round_no, agent_id)
            )""")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_research_tasks_run ON research_tasks(run_id, round_no, started_at)")
            conn.execute("""CREATE TABLE IF NOT EXISTS research_evidence (
                id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES research_runs(id) ON DELETE CASCADE,
                round_no INTEGER NOT NULL, provider TEXT NOT NULL, source_type TEXT NOT NULL,
                title TEXT NOT NULL, url TEXT NOT NULL, snippet TEXT NOT NULL DEFAULT '', citation_label TEXT NOT NULL DEFAULT '', retrieved_at TEXT NOT NULL,
                UNIQUE(run_id, round_no, provider, title, url)
            )""")
            conn.execute("""CREATE TABLE IF NOT EXISTS research_claims (
                id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES research_runs(id) ON DELETE CASCADE,
                output_id TEXT NOT NULL REFERENCES research_outputs(id) ON DELETE CASCADE,
                round_no INTEGER NOT NULL, agent_id TEXT NOT NULL, claim_text TEXT NOT NULL,
                claim_type TEXT NOT NULL, epistemic_status TEXT NOT NULL, uncertainty TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL, UNIQUE(output_id, claim_text)
            )""")
            conn.execute("""CREATE TABLE IF NOT EXISTS research_claim_evidence (
                claim_id TEXT NOT NULL REFERENCES research_claims(id) ON DELETE CASCADE,
                evidence_id TEXT NOT NULL REFERENCES research_evidence(id) ON DELETE CASCADE,
                relation TEXT NOT NULL,
                PRIMARY KEY(claim_id,evidence_id,relation)
            )""")
            conn.execute("""CREATE TABLE IF NOT EXISTS research_memory (
                id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, run_id TEXT NOT NULL REFERENCES research_runs(id) ON DELETE CASCADE,
                round_no INTEGER NOT NULL, title TEXT NOT NULL, question TEXT NOT NULL, workflow_mode TEXT NOT NULL,
                research_depth TEXT NOT NULL, agents_json TEXT NOT NULL, summary TEXT NOT NULL,
                claims_json TEXT NOT NULL DEFAULT '[]', open_questions_json TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL,
                UNIQUE(run_id,round_no)
            )""")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_research_memory_owner ON research_memory(owner_id,created_at DESC)")
            conn.execute("""CREATE TABLE IF NOT EXISTS research_memory_links (
                run_id TEXT NOT NULL REFERENCES research_runs(id) ON DELETE CASCADE, round_no INTEGER NOT NULL,
                memory_id TEXT NOT NULL REFERENCES research_memory(id) ON DELETE CASCADE,
                relevance REAL NOT NULL, created_at TEXT NOT NULL,
                PRIMARY KEY(run_id,round_no,memory_id)
            )""")
            conn.execute("""CREATE TABLE IF NOT EXISTS research_output_evidence (
                output_id TEXT NOT NULL REFERENCES research_outputs(id) ON DELETE CASCADE,
                evidence_id TEXT NOT NULL REFERENCES research_evidence(id) ON DELETE CASCADE,
                relation TEXT NOT NULL DEFAULT 'context',
                PRIMARY KEY(output_id, evidence_id)
            )""")
            run_columns = {row[1] for row in conn.execute("PRAGMA table_info(research_runs)").fetchall()}
            if "workflow_mode" not in run_columns:
                conn.execute("ALTER TABLE research_runs ADD COLUMN workflow_mode TEXT NOT NULL DEFAULT 'multidisciplinary'")
            if "research_depth" not in run_columns:
                conn.execute("ALTER TABLE research_runs ADD COLUMN research_depth TEXT NOT NULL DEFAULT 'normal'")
            if "route_reason" not in run_columns:
                conn.execute("ALTER TABLE research_runs ADD COLUMN route_reason TEXT NOT NULL DEFAULT ''")
            if "current_stage" not in run_columns:
                conn.execute("ALTER TABLE research_runs ADD COLUMN current_stage TEXT NOT NULL DEFAULT 'queued'")
            migrations = {
                "continuous_config_json": "TEXT NOT NULL DEFAULT '{}'",
                "started_at": "TEXT NOT NULL DEFAULT ''",
                "consecutive_errors": "INTEGER NOT NULL DEFAULT 0",
                "no_progress_cycles": "INTEGER NOT NULL DEFAULT 0",
                "convergence_cycles": "INTEGER NOT NULL DEFAULT 0",
                "escape_attempted": "INTEGER NOT NULL DEFAULT 0",
                "session_summary_json": "TEXT NOT NULL DEFAULT '{}'",
                "runtime_seconds_total": "REAL NOT NULL DEFAULT 0",
                "provider_id": "TEXT NOT NULL DEFAULT ''",
                "provider_type": "TEXT NOT NULL DEFAULT 'LOCAL'",
                "model_name": "TEXT NOT NULL DEFAULT ''",
            }
            for column, declaration in migrations.items():
                if column not in run_columns:
                    conn.execute(f"ALTER TABLE research_runs ADD COLUMN {column} {declaration}")
            conn.execute("UPDATE research_runs SET started_at=created_at WHERE started_at=''")
            conn.execute("""CREATE TABLE IF NOT EXISTS research_cycle_metrics (
                run_id TEXT NOT NULL REFERENCES research_runs(id) ON DELETE CASCADE,
                cycle_no INTEGER NOT NULL, metrics_json TEXT NOT NULL, created_at TEXT NOT NULL,
                PRIMARY KEY(run_id,cycle_no)
            )""")
            conn.execute("""CREATE TABLE IF NOT EXISTS research_model_calls (
                id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES research_runs(id) ON DELETE CASCADE,
                cycle_no INTEGER NOT NULL DEFAULT 0, agent_id TEXT NOT NULL DEFAULT '', purpose TEXT NOT NULL DEFAULT '',
                provider_id TEXT NOT NULL, provider_type TEXT NOT NULL, model_name TEXT NOT NULL,
                successful INTEGER NOT NULL, error_code TEXT NOT NULL DEFAULT '', retry_count INTEGER NOT NULL DEFAULT 0,
                input_tokens INTEGER, output_tokens INTEGER, total_tokens INTEGER, estimated_cost REAL, currency TEXT,
                duration_seconds REAL NOT NULL DEFAULT 0, created_at TEXT NOT NULL
            )""")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_research_model_calls_run ON research_model_calls(run_id,cycle_no,created_at)")
            conn.execute("CREATE TABLE IF NOT EXISTS research_control (id INTEGER PRIMARY KEY CHECK(id=1), emergency_stop INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL DEFAULT '')")
            conn.execute("INSERT OR IGNORE INTO research_control(id,emergency_stop,updated_at) VALUES(1,0,?)", (utc_now(),))
            evidence_columns = {row[1] for row in conn.execute("PRAGMA table_info(research_evidence)").fetchall()}
            if "citation_label" not in evidence_columns:
                conn.execute("ALTER TABLE research_evidence ADD COLUMN citation_label TEXT NOT NULL DEFAULT ''")
            # A process restart is a checkpoint, not a failed or lost run.
            conn.execute("UPDATE research_runs SET status='paused', updated_at=? WHERE status IN ('planning','running','converging','pause_requested','cancel_requested')", (utc_now(),))
            conn.commit()

    @staticmethod
    def _run_dict(row: sqlite3.Row) -> Dict[str, Any]:
        value = dict(row)
        value["agents"] = json.loads(value.pop("agents_json"))
        value["continuous_config"] = json.loads(value.get("continuous_config_json") or "{}")
        value["session_summary"] = json.loads(value.get("session_summary_json") or "{}")
        value["continuous"] = value["max_rounds"] == 0
        value["max_rounds"] = None if value["max_rounds"] == 0 else value["max_rounds"]
        return value

    def _get_row(self, run_id: str, owner_id: str) -> Optional[sqlite3.Row]:
        with connection() as conn:
            return conn.execute("SELECT * FROM research_runs WHERE id=? AND owner_id=?", (run_id, owner_id)).fetchone()

    def list_runs(self, owner_id: str) -> List[Dict[str, Any]]:
        with connection() as conn:
            rows = conn.execute("SELECT * FROM research_runs WHERE owner_id=? ORDER BY updated_at DESC", (owner_id,)).fetchall()
        return [self._run_dict(row) for row in rows]

    def get_detail(self, run_id: str, owner_id: str) -> Dict[str, Any]:
        row = self._get_row(run_id, owner_id)
        if not row:
            raise HTTPException(404, "探索任务不存在")
        with connection() as conn:
            outputs = conn.execute("SELECT * FROM research_outputs WHERE run_id=? ORDER BY round_no, created_at, agent_id", (run_id,)).fetchall()
            tasks = conn.execute("SELECT * FROM research_tasks WHERE run_id=? ORDER BY round_no, started_at, agent_id", (run_id,)).fetchall()
            evidence = conn.execute("""SELECT e.*, GROUP_CONCAT(o.agent_id) AS cited_by
                FROM research_evidence e LEFT JOIN research_output_evidence oe ON oe.evidence_id=e.id
                LEFT JOIN research_outputs o ON o.id=oe.output_id
                WHERE e.run_id=? GROUP BY e.id ORDER BY e.round_no,e.title""", (run_id,)).fetchall()
            claims = conn.execute("SELECT * FROM research_claims WHERE run_id=? ORDER BY round_no,created_at,agent_id", (run_id,)).fetchall()
            claim_evidence = conn.execute("""SELECT ce.claim_id,ce.relation,e.id,e.citation_label,e.source_type,e.title,e.url
                FROM research_claim_evidence ce JOIN research_evidence e ON e.id=ce.evidence_id
                JOIN research_claims c ON c.id=ce.claim_id WHERE c.run_id=? ORDER BY e.title""", (run_id,)).fetchall()
            memory = conn.execute("SELECT * FROM research_memory WHERE run_id=? ORDER BY round_no", (run_id,)).fetchall()
            cycle_metrics = conn.execute("SELECT cycle_no,metrics_json,created_at FROM research_cycle_metrics WHERE run_id=? ORDER BY cycle_no DESC LIMIT 12", (run_id,)).fetchall()
            all_cycle_metric_rows = conn.execute("SELECT metrics_json FROM research_cycle_metrics WHERE run_id=? ORDER BY cycle_no", (run_id,)).fetchall()
            related_memory = conn.execute("""SELECT m.id,m.title,m.question,m.round_no,m.summary,m.claims_json,m.open_questions_json,
                ml.relevance,ml.run_id,ml.round_no AS linked_round FROM research_memory_links ml
                JOIN research_memory m ON m.id=ml.memory_id WHERE ml.run_id=? ORDER BY ml.round_no,ml.relevance DESC""", (run_id,)).fetchall()
        data = self._run_dict(row)
        data["outputs"] = [{**dict(item), "sources": json.loads(item["sources_json"])} for item in outputs]
        data["tasks"] = [dict(task) for task in tasks]
        data["evidence"] = [{**dict(item), "cited_by": (item["cited_by"] or "").split(",") if item["cited_by"] else []} for item in evidence]
        links: Dict[str, List[Dict[str, str]]] = {}
        for link in claim_evidence:
            links.setdefault(link["claim_id"], []).append({key: link[key] for key in ("id", "citation_label", "source_type", "title", "url", "relation")})
        data["claims"] = [{**dict(claim), "evidence": links.get(claim["id"], [])} for claim in claims]
        data["memory"] = [{**dict(item), "agents": json.loads(item["agents_json"]), "claims": json.loads(item["claims_json"]),
                            "open_questions": json.loads(item["open_questions_json"])} for item in memory]
        data["related_memory"] = [{**dict(item), "claims": json.loads(item["claims_json"]),
                                   "open_questions": json.loads(item["open_questions_json"])} for item in related_memory]
        data["cycle_metrics"] = [{**json.loads(item["metrics_json"]), "cycle_id": item["cycle_no"], "timestamp": item["created_at"]}
                                  for item in reversed(cycle_metrics)]
        _, effective_provider_type = self._effective_resource_policy(row["id"], ContinuousResearchConfig.from_dict(data["continuous_config"], row["provider_type"]), row["provider_type"])
        total_information_gain = sum(float(json.loads(item[0]).get("information_gain_score", 0)) for item in all_cycle_metric_rows)
        data["resource_usage"] = self._resource_usage(row["id"], effective_provider_type, row["started_at"] or row["created_at"],
            int(row["current_round"]), total_information_gain)
        return data

    def get_graph(self, run_id: str, owner_id: str, limit_rounds: int = 50) -> Dict[str, Any]:
        run = self._get_row(run_id, owner_id)
        if not run:
            raise HTTPException(404, "探索任务不存在")
        round_start = max(1, int(run["current_round"]) - limit_rounds + 1)
        with connection() as conn:
            tasks = conn.execute("SELECT * FROM research_tasks WHERE run_id=? AND round_no>=? ORDER BY round_no,started_at", (run_id, round_start)).fetchall()
            outputs = conn.execute("SELECT * FROM research_outputs WHERE run_id=? AND round_no>=? ORDER BY round_no,created_at", (run_id, round_start)).fetchall()
            claims = conn.execute("SELECT * FROM research_claims WHERE run_id=? AND round_no>=? ORDER BY round_no,created_at", (run_id, round_start)).fetchall()
            evidence = conn.execute("SELECT * FROM research_evidence WHERE run_id=? AND round_no>=? ORDER BY round_no,title", (run_id, round_start)).fetchall()
            output_evidence = conn.execute("""SELECT oe.output_id,oe.evidence_id,oe.relation FROM research_output_evidence oe
                JOIN research_outputs o ON o.id=oe.output_id WHERE o.run_id=? AND o.round_no>=?""", (run_id, round_start)).fetchall()
            claim_evidence = conn.execute("""SELECT ce.claim_id,ce.evidence_id,ce.relation FROM research_claim_evidence ce
                JOIN research_claims c ON c.id=ce.claim_id WHERE c.run_id=? AND c.round_no>=?""", (run_id, round_start)).fetchall()
            memories = conn.execute("SELECT * FROM research_memory WHERE run_id=? AND round_no>=? ORDER BY round_no", (run_id, round_start)).fetchall()
            linked_memory = conn.execute("""SELECT m.* ,ml.relevance,ml.round_no AS linked_round FROM research_memory_links ml
                JOIN research_memory m ON m.id=ml.memory_id WHERE ml.run_id=? AND ml.round_no>=? ORDER BY ml.round_no,ml.relevance DESC""",
                (run_id, round_start)).fetchall()
        nodes: List[Dict[str, Any]] = [{"id": f"run:{run_id}", "type": "run", "label": run["title"], "status": run["status"]}]
        edges: List[Dict[str, str]] = []
        task_output_ids = {task["output_id"] for task in tasks if task["output_id"]}
        for task in tasks:
            node_id = f"task:{task['id']}"
            nodes.append({"id": node_id, "type": "task", "label": task["agent_id"], "stage": task["stage"],
                          "status": task["status"], "round_no": task["round_no"]})
            edges.append({"source": f"run:{run_id}", "target": node_id, "relation": "has_task"})
            if task["output_id"]:
                edges.append({"source": node_id, "target": f"output:{task['output_id']}", "relation": "produces"})
        for output in outputs:
            nodes.append({"id": f"output:{output['id']}", "type": "output", "label": output["agent_name"],
                          "status": output["status"], "round_no": output["round_no"], "preview": output["content"][:220]})
            if output["id"] not in task_output_ids:
                edges.append({"source": f"run:{run_id}", "target": f"output:{output['id']}", "relation": "has_output"})
        for claim in claims:
            nodes.append({"id": f"claim:{claim['id']}", "type": "claim", "label": claim["claim_text"],
                          "claim_type": claim["claim_type"], "epistemic_status": claim["epistemic_status"],
                          "round_no": claim["round_no"]})
            edges.append({"source": f"output:{claim['output_id']}", "target": f"claim:{claim['id']}", "relation": "asserts"})
        for item in evidence:
            nodes.append({"id": f"evidence:{item['id']}", "type": "evidence", "label": item["title"],
                          "source_type": item["source_type"], "url": item["url"], "citation_label": item["citation_label"],
                          "round_no": item["round_no"]})
        for link in output_evidence:
            edges.append({"source": f"output:{link['output_id']}", "target": f"evidence:{link['evidence_id']}", "relation": link["relation"]})
        for link in claim_evidence:
            edges.append({"source": f"claim:{link['claim_id']}", "target": f"evidence:{link['evidence_id']}", "relation": link["relation"]})
        for memory in memories:
            nodes.append({"id": f"memory:{memory['id']}", "type": "memory", "label": memory["title"],
                          "round_no": memory["round_no"], "preview": memory["summary"][:220]})
            edges.append({"source": f"run:{run_id}", "target": f"memory:{memory['id']}", "relation": "records"})
        for memory in linked_memory:
            memory_node_id = f"memory:{memory['id']}"
            if all(node["id"] != memory_node_id for node in nodes):
                nodes.append({"id": memory_node_id, "type": "memory", "label": memory["title"],
                              "round_no": memory["round_no"], "preview": memory["summary"][:220], "external_run": True})
            edges.append({"source": f"run:{run_id}", "target": memory_node_id, "relation": "learns_from",
                          "relevance": str(memory["relevance"])})
        round_end = max(round_start, int(run["current_round"]) + (1 if run["status"] == "running" else 0))
        return {"run_id": run_id, "round_start": round_start, "round_end": round_end,
                "nodes": nodes, "edges": edges}

    @staticmethod
    def _memory_terms(text: str) -> set[str]:
        terms = set(re.findall(r"[a-z0-9]{2,}", text.casefold()))
        for phrase in re.findall(r"[\u4e00-\u9fff]+", text):
            terms.update(phrase[index:index + 2] for index in range(max(0, len(phrase) - 1)))
        return terms

    def _retrieve_memories(self, owner_id: str, run_id: str, round_no: int, question: str, limit: int = 3) -> List[Dict[str, Any]]:
        query_terms = self._memory_terms(question)
        if not query_terms:
            return []
        with connection() as conn:
            rows = conn.execute("SELECT * FROM research_memory WHERE owner_id=? AND run_id<>? ORDER BY created_at DESC LIMIT 300",
                                (owner_id, run_id)).fetchall()
            scored = []
            for row in rows:
                memory_terms = self._memory_terms(row["question"] + " " + row["title"] + " " + row["summary"][:1200])
                relevance = len(query_terms & memory_terms) / max(1, len(query_terms))
                if relevance >= 0.12:
                    value = dict(row)
                    value["relevance"] = round(relevance, 3)
                    value["claims"] = json.loads(row["claims_json"])
                    value["open_questions"] = json.loads(row["open_questions_json"])
                    scored.append(value)
            selected = sorted(scored, key=lambda item: (item["relevance"], item["created_at"]), reverse=True)[:limit]
            now = utc_now()
            for item in selected:
                conn.execute("""INSERT OR REPLACE INTO research_memory_links(run_id,round_no,memory_id,relevance,created_at)
                    VALUES(?,?,?,?,?)""", (run_id, round_no, item["id"], item["relevance"], now))
            conn.commit()
        return selected

    def _save_memory(self, run_id: str, round_no: int, summary: str):
        run = self._state(run_id)
        if not run:
            return
        with connection() as conn:
            claim_rows = conn.execute("SELECT id,agent_id,claim_text,claim_type,epistemic_status FROM research_claims WHERE run_id=? AND round_no=? ORDER BY agent_id,created_at",
                                      (run_id, round_no)).fetchall()
            claims = []
            for claim in claim_rows:
                evidence = conn.execute("""SELECT e.citation_label,e.title,e.url,ce.relation FROM research_claim_evidence ce
                    JOIN research_evidence e ON e.id=ce.evidence_id WHERE ce.claim_id=? ORDER BY e.title""", (claim["id"],)).fetchall()
                claims.append({"agent_id": claim["agent_id"], "text": claim["claim_text"], "type": claim["claim_type"],
                               "epistemic_status": claim["epistemic_status"], "evidence": [dict(item) for item in evidence]})
            open_questions = [item for item in claims if item["type"] in ("hypothesis", "prediction", "limitation", "counterevidence")]
            conn.execute("""INSERT INTO research_memory(id,owner_id,run_id,round_no,title,question,workflow_mode,research_depth,
                agents_json,summary,claims_json,open_questions_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(run_id,round_no) DO UPDATE SET summary=excluded.summary,claims_json=excluded.claims_json,
                open_questions_json=excluded.open_questions_json,created_at=excluded.created_at""",
                (uuid.uuid4().hex, run["owner_id"], run_id, round_no, run["title"], run["question"], run["workflow_mode"],
                 run["research_depth"], run["agents_json"], summary, json.dumps(claims, ensure_ascii=False),
                 json.dumps(open_questions, ensure_ascii=False), utc_now()))
            conn.commit()

    def _record_cycle_metrics(self, run_id: str, cycle_no: int, summary: str,
                              config: ContinuousResearchConfig) -> Dict[str, Any]:
        store = getattr(self.retriever, "store", None)
        embed_method = getattr(store, "_embed", None)
        embed = (lambda texts: embed_method(texts)) if callable(embed_method) else None
        with connection() as conn:
            claims = [dict(row) for row in conn.execute("SELECT claim_text,claim_type,agent_id FROM research_claims WHERE run_id=? AND round_no=?", (run_id, cycle_no))]
            prior_claims = [dict(row) for row in conn.execute("SELECT claim_text,claim_type,agent_id FROM research_claims WHERE run_id=? AND round_no>=? AND round_no<? ORDER BY round_no DESC LIMIT 500", (run_id, max(1, cycle_no - config.loop_detection_window + 1), cycle_no))]
            evidence = [dict(row) for row in conn.execute("SELECT url,title FROM research_evidence WHERE run_id=? AND round_no=?", (run_id, cycle_no))]
            prior_urls = {row[0] for row in conn.execute("SELECT DISTINCT url FROM research_evidence WHERE run_id=? AND round_no<?", (run_id, cycle_no))}
            connections = conn.execute("SELECT COUNT(*) FROM research_output_evidence oe JOIN research_outputs o ON o.id=oe.output_id WHERE o.run_id=? AND o.round_no=?", (run_id, cycle_no)).fetchone()[0]
            completed = conn.execute("SELECT COUNT(*) FROM research_tasks WHERE run_id=? AND round_no=? AND status='completed'", (run_id, cycle_no)).fetchone()[0]
            tasks_created = conn.execute("SELECT COUNT(*) FROM research_tasks WHERE run_id=? AND round_no=?", (run_id, cycle_no)).fetchone()[0]
            executable = conn.execute("SELECT COUNT(*) FROM research_tasks WHERE run_id=? AND round_no=? AND status IN ('queued','running')", (run_id, cycle_no)).fetchone()[0]
            open_rows = conn.execute("SELECT claim_text FROM research_claims WHERE run_id=? AND round_no=? AND claim_type IN ('hypothesis','prediction','limitation','counterevidence')", (run_id, cycle_no)).fetchall()
            old_open = [str(row[0]) for row in conn.execute("SELECT open_questions_json FROM research_memory WHERE run_id=? AND round_no<? ORDER BY round_no DESC LIMIT 1", (run_id, cycle_no)).fetchall()]
            output_agents = [row[0] for row in conn.execute("SELECT agent_id FROM research_outputs WHERE run_id=? AND round_no=? AND status='completed'", (run_id, cycle_no)).fetchall()]
            past_cycles = [json.loads(row[0]) for row in conn.execute("SELECT metrics_json FROM research_cycle_metrics WHERE run_id=? ORDER BY cycle_no DESC LIMIT ?", (run_id, config.loop_detection_window - 1)).fetchall()]
        blocked = [{"question": str(row[0]), "reason": ProgressTracker.blocked_reason(str(row[0]))}
                   for row in open_rows if ProgressTracker.blocked_reason(str(row[0]))]
        resolved_terms = ("已解决", "得到回答", "已回答", "被证伪", "已排除")
        resolved_questions = min(len(json.loads(old_open[0])) if old_open else 0,
                                 sum(1 for term in resolved_terms if term in summary))
        metrics = ProgressTracker(config.weights, config.duplicate_similarity_threshold, embed).measure(
            claims, evidence, prior_claims, prior_urls, connections, resolved_questions)
        metrics.update({"cycle_id": cycle_no, "timestamp": utc_now(), "agents_used": sorted(set(output_agents)),
                        "tasks_completed": int(completed), "tasks_created": int(tasks_created),
                        "blocked_questions": blocked, "executable_tasks": int(executable),
                        "signature": ProgressTracker.signature(claims, summary)})
        loop = ResearchLoopDetector(config.loop_threshold, config.loop_detection_window)
        metrics["loop_score"] = round(loop.score([*reversed(past_cycles), metrics]), 3)
        run = self._state(run_id)
        if run:
            _, effective_provider_type = self._effective_resource_policy(run_id, ContinuousResearchConfig.from_dict(json.loads(run["continuous_config_json"] or "{}"), run["provider_type"]), run["provider_type"])
            with connection() as conn:
                all_metrics = [json.loads(row[0]) for row in conn.execute("SELECT metrics_json FROM research_cycle_metrics WHERE run_id=?", (run_id,)).fetchall()]
            metrics["resource_usage"] = self._resource_usage(run_id, effective_provider_type, run["started_at"] or run["created_at"], cycle_no,
                sum(float(item.get("information_gain_score", 0)) for item in all_metrics) + float(metrics.get("information_gain_score", 0)))
        with connection() as conn:
            conn.execute("INSERT OR REPLACE INTO research_cycle_metrics(run_id,cycle_no,metrics_json,created_at) VALUES(?,?,?,?)",
                         (run_id, cycle_no, json.dumps(metrics, ensure_ascii=False), metrics["timestamp"]))
        return metrics

    @staticmethod
    def _latest_model_error(run_id: str) -> Optional[Dict[str, Any]]:
        with connection() as conn:
            row = conn.execute("SELECT successful,error_code,provider_type,provider_id,model_name,created_at FROM research_model_calls WHERE run_id=? ORDER BY created_at DESC LIMIT 1", (run_id,)).fetchone()
        return dict(row) if row and not row["successful"] and row["error_code"] else None

    @staticmethod
    def _effective_resource_policy(run_id: str, config: ContinuousResearchConfig, default_type: str):
        with connection() as conn:
            remote_used = conn.execute("SELECT 1 FROM research_model_calls WHERE run_id=? AND provider_type='REMOTE' LIMIT 1", (run_id,)).fetchone()
        if not remote_used or default_type.upper() == "REMOTE":
            return config, default_type.upper()
        values = config.to_dict()
        values.update({"provider_type": "REMOTE", "max_model_calls": min(config.max_model_calls or 100, 100),
                       "max_session_tokens": min(config.max_session_tokens or 500_000, 500_000),
                       "max_session_cost": config.max_session_cost if config.max_session_cost is not None else 5.0})
        return ContinuousResearchConfig.from_dict(values, "REMOTE"), "REMOTE"

    @staticmethod
    def _emergency_stop_requested() -> bool:
        with connection() as conn:
            row = conn.execute("SELECT emergency_stop FROM research_control WHERE id=1").fetchone()
        return bool(row and row[0])

    def set_emergency_stop(self, enabled: bool):
        now = utc_now()
        with connection() as conn:
            conn.execute("UPDATE research_control SET emergency_stop=?,updated_at=? WHERE id=1", (int(enabled), now))
        return {"enabled": enabled, "updated_at": now}

    @staticmethod
    def emergency_stop_status() -> Dict[str, Any]:
        with connection() as conn:
            row = conn.execute("SELECT emergency_stop,updated_at FROM research_control WHERE id=1").fetchone()
        return {"enabled": bool(row[0]), "updated_at": row[1]} if row else {"enabled": False, "updated_at": ""}

    def _session_summary(self, run: sqlite3.Row, metrics_rows: List[Dict[str, Any]], reason: str) -> Dict[str, Any]:
        self._checkpoint_runtime(run["id"])
        with connection() as conn:
            metrics_rows = [json.loads(row[0]) for row in conn.execute("SELECT metrics_json FROM research_cycle_metrics WHERE run_id=? ORDER BY cycle_no", (run["id"],)).fetchall()]
        totals: Dict[str, int] = {}
        for item in metrics_rows:
            for key in ("new_claims", "new_evidence", "new_counterexamples", "new_hypotheses", "new_predictions", "new_experiments", "new_mathematical_models", "resolved_questions"):
                totals[key] = totals.get(key, 0) + int(item.get(key, 0))
        blocked: Dict[str, int] = {}
        for cycle in metrics_rows:
            for item in cycle.get("blocked_questions", []):
                code = item.get("reason", "INSUFFICIENT_EVIDENCE")
                blocked[code] = blocked.get(code, 0) + 1
        _, effective_provider_type = self._effective_resource_policy(run["id"], ContinuousResearchConfig.from_dict(json.loads(run["continuous_config_json"] or "{}"), run["provider_type"]), run["provider_type"])
        resources = self._resource_usage(run["id"], effective_provider_type, run["started_at"] or run["created_at"],
                                        int(run["current_round"]), sum(float(item.get("information_gain_score", 0)) for item in metrics_rows))
        elapsed = max(0, int(resources["runtime_seconds"] // 60))
        open_problems = list(dict.fromkeys(str(question.get("question", "")) for cycle in metrics_rows
            for question in cycle.get("blocked_questions", []) if question.get("question")))[:30]
        with connection() as conn:
            memory_rows = conn.execute("SELECT open_questions_json FROM research_memory WHERE run_id=? ORDER BY round_no DESC LIMIT 12", (run["id"],)).fetchall()
            claims = conn.execute("SELECT claim_text,claim_type FROM research_claims WHERE run_id=? ORDER BY round_no DESC,created_at DESC LIMIT 500", (run["id"],)).fetchall()
        for memory_row in memory_rows:
            try:
                for item in json.loads(memory_row[0] or "[]"):
                    text = str(item.get("text", "")).strip()
                    if text and text not in open_problems:
                        open_problems.append(text)
            except (ValueError, TypeError, AttributeError):
                continue
            if len(open_problems) >= 30:
                break
        return {"provider_id": run["provider_id"], "provider_type": run["provider_type"], "model_name": run["model_name"],
                "duration_minutes": elapsed, "cycles": int(run["current_round"]),
                "agent_calls": resources["model_calls"], "resource_usage": resources,
                "totals": totals, "blocked_questions": blocked, "open_problems": open_problems,
                "key_findings": [row["claim_text"] for row in claims if row["claim_type"] == "finding"][:30],
                "counterexamples": [row["claim_text"] for row in claims if row["claim_type"] == "counterevidence"][:30],
                "hypotheses": [row["claim_text"] for row in claims if row["claim_type"] == "hypothesis"][:30],
                "experiments": [row["claim_text"] for row in claims if any(term in row["claim_text"] for term in ("实验", "experiment"))][:30],
                "mathematical_models": [row["claim_text"] for row in claims if any(term in row["claim_text"] for term in ("方程", "数学模型", "形式化模型", "数学表达"))][:30],
                "latest_synthesis": str(run["summary"] or "")[:6000],
                "reason": reason, "resume_conditions": ["添加新的知识库文档", "补充实验或观测数据", "提出新的研究方向", "更换或升级模型", "用户手动恢复"]}

    def _final_escape_cycle(self, run: sqlite3.Row, cycle_no: int, metrics: List[Dict[str, Any]]) -> Dict[str, Any]:
        context = json.dumps({"question": run["question"], "title": run["title"], "recent_cycles": metrics[-5:]}, ensure_ascii=False)[:9000]
        roles = [
            ("escape_planner", "Research Planner", AGENTS["foundations"].system_prompt, "提出尚未执行的高价值研究路径和可执行下一步。"),
            ("escape_skeptic", "Skeptic / Counterexample Agent", AGENTS["critic"].system_prompt, "寻找最强反例、替代理论和能推翻当前判断的观察。"),
            ("escape_open_questions", "Open Problems Agent", AGENTS["experimental_methods"].system_prompt, "提出仍未解决且依赖实验、外部数据或新模型的关键问题。"),
        ]
        answers = []
        for agent_id, label, system, instruction in roles:
            content = self._call_model(system + "请严格区分证据与推测，输出简短 Markdown。", f"研究进入休眠前的最终逃逸探索。\n{context}\n\n{instruction}", 0.2, 1000,
                run_id=run["id"], cycle_no=cycle_no, agent_id=agent_id, purpose="final_escape")
            self._save_output(run["id"], cycle_no, agent_id, content, [], "completed")
            answers.append(f"## {label}\n{content}")
        merged = "\n\n".join(answers)
        meta_prompt = ("基于这些规划、反例审查与开放问题分析，判断是否有真正不同于历史内容的高价值方向。只返回 JSON："
            '{"new_direction_found":true/false,"reason":"...","next_question":"..."}\n' + merged[:9000])
        meta = self._call_model(AGENTS["synthesis"].system_prompt, meta_prompt, 0.1, 700,
            run_id=run["id"], cycle_no=cycle_no, agent_id="escape_meta_research", purpose="final_escape_judge")
        self._save_output(run["id"], cycle_no, "escape_meta_research", meta, [], "completed")
        match = re.search(r"\{.*\}", meta, re.DOTALL)
        try:
            result = json.loads(match.group(0)) if match else {}
        except (ValueError, TypeError):
            result = {}
        next_question = str(result.get("next_question", "")).strip()
        old_questions = [str(item.get("signature", "")) for item in metrics[-5:]]
        novelty = NoveltyDetector(0.92).max_similarity(next_question, old_questions)
        found = bool(result.get("new_direction_found")) and len(next_question) >= 12 and novelty < 0.92
        return {"new_direction_found": found, "reason": str(result.get("reason", ""))[:500],
                "next_question": next_question, "output": merged + "\n\n## Meta Research\n" + meta}

    def _apply_continuous_policy(self, run: sqlite3.Row, metrics: Dict[str, Any]) -> str:
        """Apply automatic continuation/stop rules after a completed continuous cycle."""
        config = ContinuousResearchConfig.from_dict(json.loads(run["continuous_config_json"] or "{}"))
        if run["max_rounds"] != 0:
            return "running"
        started = run["started_at"] or run["created_at"]
        elapsed = self._runtime_seconds(run["id"]) / 60
        with connection() as conn:
            current = conn.execute("SELECT * FROM research_runs WHERE id=?", (run["id"],)).fetchone()
            rows = conn.execute("SELECT metrics_json FROM research_cycle_metrics WHERE run_id=? ORDER BY cycle_no DESC LIMIT 12", (run["id"],)).fetchall()
        if not current:
            return "stopped"
        config, effective_provider_type = self._effective_resource_policy(current["id"], config, current["provider_type"])
        history = [json.loads(row[0]) for row in reversed(rows)]
        no_progress = ProgressTracker.advance_no_progress(int(current["no_progress_cycles"]),
            float(metrics.get("information_gain_score", 0)), config.min_information_gain)
        was_converging = current["status"] == "converging"
        convergence_cycles = int(current["convergence_cycles"]) + 1 if was_converging else 0
        if float(metrics.get("information_gain_score", 0)) >= config.min_information_gain:
            convergence_cycles = 0
        effective_config, effective_provider_type = self._effective_resource_policy(current["id"], config, current["provider_type"])
        resource_usage = self._resource_usage(current["id"], effective_provider_type, started, int(current["current_round"]),
            sum(float(item.get("information_gain_score", 0)) for item in history))
        latest_error = self._latest_model_error(current["id"])
        pre_decision = ContinuousResearchController(effective_config).decide(state=current["status"], elapsed_minutes=elapsed,
            cycles=int(current["current_round"]), consecutive_errors=int(current["consecutive_errors"]),
            no_progress_cycles=no_progress, convergence_cycles=convergence_cycles, recent_metrics=history,
            emergency_stop=self._emergency_stop_requested(), provider_type=effective_provider_type,
            resource_usage=resource_usage, latest_error=latest_error)
        non_advisory_stops = {"MANUAL_STOP", "EMERGENCY_STOP", "MAX_RUNTIME", "MAX_CYCLES", "TOO_MANY_ERRORS",
            "MAX_CONSECUTIVE_ERRORS", "TOKEN_BUDGET_EXHAUSTED", "COST_BUDGET_EXHAUSTED", "CALL_BUDGET_EXHAUSTED",
            "API_QUOTA_EXHAUSTED", "API_RATE_LIMITED", "API_AUTH_FAILED", "API_SERVER_ERROR", "API_NETWORK_ERROR",
            "MODEL_UNAVAILABLE", "LOCAL_RESOURCE_ERROR"}
        if pre_decision.reason_code in non_advisory_stops and (pre_decision.should_stop or pre_decision.should_sleep):
            summary = self._session_summary(current, history, pre_decision.reason)
            summary["reason_code"] = pre_decision.reason_code
            with connection() as conn:
                conn.execute("UPDATE research_runs SET status=?,error=?,no_progress_cycles=?,convergence_cycles=?,session_summary_json=?,updated_at=? WHERE id=?",
                    (pre_decision.next_state, pre_decision.reason, no_progress, convergence_cycles,
                     json.dumps(summary, ensure_ascii=False), utc_now(), current["id"]))
            return pre_decision.next_state
        judge = None
        if config.judge_enabled and (no_progress >= config.no_progress_patience - 1 or was_converging):
            try:
                judge_call = lambda system, prompt, temperature, max_tokens: self._call_model(system, prompt, temperature, max_tokens,
                    run_id=current["id"], cycle_no=int(current["current_round"]), agent_id="continuation_judge", purpose="continuation_judge")
                judge = ResearchContinuationJudge.evaluate({"question": current["question"], "round": current["current_round"],
                    "recent_metrics": history[-5:], "recent_summary": current["summary"][-1800:]}, judge_call)
                metrics["continuation_judge"] = judge
                with connection() as conn:
                    conn.execute("UPDATE research_cycle_metrics SET metrics_json=? WHERE run_id=? AND cycle_no=?",
                                 (json.dumps(metrics, ensure_ascii=False), current["id"], current["current_round"]))
            except Exception:
                judge = None
        decision = ContinuousResearchController(effective_config).decide(state=current["status"], elapsed_minutes=elapsed,
            cycles=int(current["current_round"]), consecutive_errors=int(current["consecutive_errors"]),
            no_progress_cycles=no_progress, convergence_cycles=convergence_cycles, recent_metrics=history,
            emergency_stop=self._emergency_stop_requested(), judge=judge, provider_type=effective_provider_type,
            resource_usage=resource_usage, latest_error=latest_error)
        now = utc_now()
        if decision.should_stop:
            with connection() as conn:
                conn.execute("UPDATE research_runs SET status=?,error=?,no_progress_cycles=?,convergence_cycles=?,session_summary_json=?,updated_at=? WHERE id=?",
                    (decision.next_state, decision.reason, no_progress, convergence_cycles,
                     json.dumps(self._session_summary(current, history, decision.reason), ensure_ascii=False), now, current["id"]))
            return decision.next_state
        if decision.next_state == "running" and decision.reason_code in ("NEW_PROGRESS", "JUDGE_REPLAN"):
            no_progress = 0
            convergence_cycles = 0
        soft_sleep_reasons = {"NO_INFORMATION_GAIN", "RESEARCH_CONVERGED", "LOOP_DETECTED", "NO_EXECUTABLE_TASKS", "ALL_TASKS_BLOCKED"}
        if decision.should_sleep and decision.reason_code in soft_sleep_reasons and config.final_escape_cycle_enabled and not current["escape_attempted"]:
            with connection() as conn:
                conn.execute("UPDATE research_runs SET escape_attempted=1,updated_at=? WHERE id=?", (now, current["id"]))
            escape = {"new_direction_found": False, "reason": "最终逃逸探索未能完成", "next_question": ""}
            try:
                escape = self._final_escape_cycle(current, int(current["current_round"]), history)
            except Exception as exc:
                escape["reason"] = str(exc)[:400]
            if escape.get("new_direction_found"):
                metrics["escape_cycle"] = {key: escape.get(key) for key in ("new_direction_found", "reason", "next_question")}
                with connection() as conn:
                    conn.execute("UPDATE research_cycle_metrics SET metrics_json=? WHERE run_id=? AND cycle_no=?", (json.dumps(metrics, ensure_ascii=False), current["id"], current["current_round"]))
                    conn.execute("UPDATE research_runs SET status='running',error='最终反例探索找到新方向，继续研究',no_progress_cycles=0,convergence_cycles=0,escape_attempted=0,updated_at=? WHERE id=?", (utc_now(), current["id"]))
                return "running"
        if decision.should_sleep:
            reason = decision.reason
            with connection() as conn:
                latest = conn.execute("SELECT * FROM research_runs WHERE id=?", (current["id"],)).fetchone()
                summary = self._session_summary(latest, history, reason)
                summary["final_escape"] = metrics.get("escape_cycle", {"attempted": bool(current["escape_attempted"] or config.final_escape_cycle_enabled)})
                conn.execute("UPDATE research_runs SET status='sleeping',error=?,no_progress_cycles=?,convergence_cycles=?,session_summary_json=?,updated_at=? WHERE id=?",
                    (reason, no_progress, convergence_cycles, json.dumps(summary, ensure_ascii=False), utc_now(), current["id"]))
            return "sleeping"
        with connection() as conn:
            conn.execute("UPDATE research_runs SET status=?,error=?,no_progress_cycles=?,convergence_cycles=?,consecutive_errors=0,updated_at=? WHERE id=?",
                (decision.next_state, decision.reason, no_progress, convergence_cycles, now, current["id"]))
        return decision.next_state

    def create(self, payload: CreateResearchRun, owner_id: str) -> Dict[str, Any]:
        try:
            route = AGENT_ROUTER.route(payload.question, payload.workflow_mode, payload.research_depth, payload.agents)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        agents = route.agent_ids
        run_id, now = uuid.uuid4().hex, utc_now()
        provider_info = model_provider_registry.describe_research_provider()
        provider_type = str(provider_info["provider_type"])
        continuous_config = ContinuousResearchConfig.from_dict(payload.continuous_config, provider_type).to_dict()
        title = payload.title.strip() or payload.question.strip().splitlines()[0][:100]
        with connection() as conn:
            conn.execute("""INSERT INTO research_runs(
                id,owner_id,title,question,agents_json,max_rounds,status,workflow_mode,research_depth,route_reason,created_at,updated_at,
                continuous_config_json,started_at,provider_id,provider_type,model_name
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (run_id, owner_id, title, payload.question.strip(), json.dumps(agents), payload.max_rounds,
                "planning", route.workflow_mode, route.research_depth, route.route_reason, now, now, json.dumps(continuous_config), now,
                provider_info["provider_id"], provider_type, provider_info["model_name"]))
            conn.commit()
        self._start(run_id)
        return self._run_dict(self._get_row(run_id, owner_id))

    def _start(self, run_id: str):
        with self._lock:
            current = self._threads.get(run_id)
            if current and current.is_alive():
                return
            thread = threading.Thread(target=self._work, args=(run_id,), name=f"science-explore-{run_id[:8]}", daemon=True)
            self._threads[run_id] = thread
            thread.start()

    def control(self, run_id: str, owner_id: str, action: str) -> Dict[str, Any]:
        if not self._get_row(run_id, owner_id):
            raise HTTPException(404, "探索任务不存在")
        with connection() as conn:
            row = conn.execute("SELECT status,current_round FROM research_runs WHERE id=?", (run_id,)).fetchone()
            status = row["status"]
            if action == "pause" and status in ("planning", "running", "converging", "queued"):
                conn.execute("UPDATE research_runs SET status='pause_requested', updated_at=? WHERE id=?", (utc_now(), run_id))
            elif action == "stop" and status in ("planning", "running", "converging", "queued", "pause_requested"):
                conn.execute("UPDATE research_runs SET status='cancel_requested', updated_at=? WHERE id=?", (utc_now(), run_id))
            elif action == "resume" and status in ("paused", "failed", "cancelled", "sleeping", "stopped"):
                # A failed synthesis is stored after current_round advances.
                # Roll that checkpoint back one round so resume retries the
                # saved synthesis instead of silently skipping it.
                failed_synthesis = conn.execute(
                    "SELECT 1 FROM research_outputs WHERE run_id=? AND round_no=? AND agent_id='synthesis' AND status='failed'",
                    (run_id, row["current_round"]),
                ).fetchone()
                if failed_synthesis and row["current_round"] > 0:
                    conn.execute("UPDATE research_runs SET current_round=current_round-1 WHERE id=?", (run_id,))
                conn.execute("UPDATE research_runs SET status='running', error='', no_progress_cycles=0, convergence_cycles=0, consecutive_errors=0, escape_attempted=0, started_at=?, updated_at=? WHERE id=?", (utc_now(), utc_now(), run_id))
                conn.commit()
                self._start(run_id)
                return self._run_dict(self._get_row(run_id, owner_id))
            else:
                raise HTTPException(409, f"当前任务状态不支持此操作：{status}")
            conn.commit()
        return self._run_dict(self._get_row(run_id, owner_id))

    def _state(self, run_id: str) -> Optional[sqlite3.Row]:
        with connection() as conn:
            return conn.execute("SELECT * FROM research_runs WHERE id=?", (run_id,)).fetchone()

    @staticmethod
    def _existing_output(run_id: str, round_no: int, agent_id: str) -> Optional[sqlite3.Row]:
        with connection() as conn:
            return conn.execute("SELECT * FROM research_outputs WHERE run_id=? AND round_no=? AND agent_id=?", (run_id, round_no, agent_id)).fetchone()

    @staticmethod
    def _task_stage(agent_id: str) -> str:
        if agent_id.startswith("escape_"):
            return "escape"
        if agent_id == "retrieval":
            return "retrieval"
        if agent_id == "debate_critic":
            return "debate"
        if agent_id == "synthesis":
            return "synthesis"
        return "expert_analysis"

    def _set_task(self, run_id: str, round_no: int, agent_id: str, status: str, *, output_id: Optional[str] = None, error: str = ""):
        now = utc_now()
        stage = self._task_stage(agent_id)
        with connection() as conn:
            conn.execute("""INSERT INTO research_tasks(id,run_id,round_no,stage,agent_id,status,output_id,error,started_at,completed_at)
                VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(run_id,round_no,agent_id) DO UPDATE SET
                stage=excluded.stage,status=excluded.status,output_id=COALESCE(excluded.output_id,research_tasks.output_id),
                error=excluded.error,attempt=research_tasks.attempt + CASE WHEN excluded.status='queued' AND research_tasks.status='failed' THEN 1 ELSE 0 END,
                started_at=CASE WHEN excluded.status='running' THEN excluded.started_at ELSE research_tasks.started_at END,
                completed_at=excluded.completed_at""",
                (uuid.uuid4().hex, run_id, round_no, stage, agent_id, status, output_id, error, now, now if status in ("completed", "failed") else None))
            conn.execute("UPDATE research_runs SET current_stage=?,updated_at=? WHERE id=?", (stage, now, run_id))
            conn.commit()

    def _execute_agent(self, run_id: str, round_no: int, agent_id: str, system: str, prompt: str,
                       temperature: float, max_tokens: int) -> str:
        self._set_task(run_id, round_no, agent_id, "running")
        return self._call_model(system, prompt, temperature, max_tokens, run_id=run_id, cycle_no=round_no,
                                agent_id=agent_id, purpose="agent")

    @staticmethod
    def _source_type(source: Dict[str, Any]) -> str:
        explicit = source.get("source_type")
        if explicit:
            return str(explicit)
        provider = str(source.get("provider", "")).casefold()
        if "本地知识库" in provider:
            return "theory_internal"
        if provider == "crossref":
            return "bibliographic_record"
        if "arxiv" in provider:
            return "preprint"
        return "web_reference"

    @staticmethod
    def _extract_claims(content: str) -> List[Dict[str, str]]:
        """Extract only statements explicitly tagged by the agent prompt."""
        pattern = re.compile(r"^\s*(?:[-*•]\s*)?\[(finding|internal|external|hypothesis|prediction|counterevidence|limitation)\]\s*(.+?)\s*$", re.IGNORECASE)
        claims = []
        for line in content.splitlines():
            match = pattern.match(line)
            if not match:
                continue
            tag, text = match.group(1).casefold(), match.group(2).strip()
            if len(text) < 12:
                continue
            if tag in ("finding", "internal", "external"):
                claim_type = "finding"
            else:
                claim_type = tag
            epistemic_status = {
                "internal": "theory_internal",
                "external": "external_source",
                "hypothesis": "hypothesis",
                "prediction": "hypothesis",
                "counterevidence": "model_inference",
                "limitation": "model_inference",
            }.get(tag, "model_inference")
            claims.append({"text": text[:2000], "claim_type": claim_type, "epistemic_status": epistemic_status})
        return claims[:20]

    def _save_output(self, run_id: str, round_no: int, agent_id: str, content: str, sources: List[Dict[str, str]], status: str = "completed"):
        now = utc_now()
        output_id = uuid.uuid4().hex
        with connection() as conn:
            conn.execute("""INSERT INTO research_outputs(id,run_id,round_no,agent_id,agent_name,status,content,sources_json,created_at)
                VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(run_id,round_no,agent_id) DO UPDATE SET
                status=excluded.status,content=excluded.content,sources_json=excluded.sources_json,created_at=excluded.created_at""",
                (output_id, run_id, round_no, agent_id, AGENTS[agent_id].name if agent_id in AGENTS else agent_id.replace("_", " ").title(), status, content, json.dumps(sources, ensure_ascii=False), now))
            saved = conn.execute("SELECT id FROM research_outputs WHERE run_id=? AND round_no=? AND agent_id=?", (run_id, round_no, agent_id)).fetchone()
            output_id = saved["id"]
            evidence_by_label: Dict[str, str] = {}
            for source in sources:
                provider = str(source.get("provider", "Unknown"))
                title = str(source.get("title", "Untitled source"))
                url = str(source.get("url", ""))
                source_type = self._source_type(source)
                snippet = str(source.get("snippet", source.get("content", "")))[:4000]
                citation_label = str(source.get("citation_label", ""))
                evidence_id = uuid.uuid4().hex
                conn.execute("""INSERT OR IGNORE INTO research_evidence(id,run_id,round_no,provider,source_type,title,url,snippet,citation_label,retrieved_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?)""", (evidence_id, run_id, round_no, provider, source_type, title, url, snippet, citation_label, now))
                saved_evidence = conn.execute("SELECT id FROM research_evidence WHERE run_id=? AND round_no=? AND provider=? AND title=? AND url=?",
                    (run_id, round_no, provider, title, url)).fetchone()
                if saved_evidence and status == "completed":
                    conn.execute("INSERT OR IGNORE INTO research_output_evidence(output_id,evidence_id,relation) VALUES(?,?,?)",
                        (output_id, saved_evidence["id"], "context"))
                    if citation_label:
                        evidence_by_label[citation_label] = saved_evidence["id"]
            if status == "completed":
                prior_claims = conn.execute("SELECT id FROM research_claims WHERE output_id=?", (output_id,)).fetchall()
                for prior_claim in prior_claims:
                    conn.execute("DELETE FROM research_claim_evidence WHERE claim_id=?", (prior_claim["id"],))
                conn.execute("DELETE FROM research_claims WHERE output_id=?", (output_id,))
                for claim in self._extract_claims(content):
                    claim_id = uuid.uuid4().hex
                    conn.execute("""INSERT INTO research_claims(id,run_id,output_id,round_no,agent_id,claim_text,claim_type,epistemic_status,uncertainty,created_at)
                        VALUES(?,?,?,?,?,?,?,?,?,?)""", (claim_id, run_id, output_id, round_no, agent_id, claim["text"], claim["claim_type"], claim["epistemic_status"], "", now))
                    labels = set(re.findall(r"\[((?:W|K)\d+)\]", claim["text"]))
                    relation = "contradicts" if claim["claim_type"] == "counterevidence" else "supports"
                    for label in labels:
                        evidence_id = evidence_by_label.get(label)
                        if evidence_id:
                            conn.execute("INSERT OR IGNORE INTO research_claim_evidence(claim_id,evidence_id,relation) VALUES(?,?,?)",
                                (claim_id, evidence_id, relation))
            stage = self._task_stage(agent_id)
            conn.execute("""INSERT INTO research_tasks(id,run_id,round_no,stage,agent_id,status,output_id,error,started_at,completed_at)
                VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(run_id,round_no,agent_id) DO UPDATE SET
                stage=excluded.stage,status=excluded.status,output_id=excluded.output_id,error=excluded.error,completed_at=excluded.completed_at""",
                (uuid.uuid4().hex, run_id, round_no, stage, agent_id, status, output_id, content[:500] if status == "failed" else "", now, now))
            conn.execute("UPDATE research_runs SET current_stage=?,updated_at=? WHERE id=?", (stage, now, run_id))
            conn.commit()

    @staticmethod
    def _format_sources(web_sources: List[Dict[str, str]], local_sources: List[Dict[str, str]]) -> str:
        parts = []
        for i, source in enumerate(web_sources[:4], 1):
            parts.append(f"[W{i}] {source['title'][:150]} ({source['provider']})\nURL: {source['url']}\n{source['snippet'][:320]}")
        for i, source in enumerate(local_sources[:2], 1):
            parts.append(f"[K{i}] 本地资料：{source['metadata']['filename']}\n{source['content'][:450]}")
        return "\n\n".join(parts)[:3600] if parts else "本轮没有检索到可用外部或本地资料。请谨慎说明证据限制。"

    @staticmethod
    def _reserve_model_call(run_id: Optional[str], cycle_no: Optional[int], agent_id: str, purpose: str,
                            provider_id: str, provider_type: str, model_name: str) -> Optional[str]:
        if not run_id:
            return None
        call_id = uuid.uuid4().hex
        with connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            run = conn.execute("SELECT max_rounds,provider_type,continuous_config_json FROM research_runs WHERE id=?", (run_id,)).fetchone()
            if run and int(run["max_rounds"]) == 0:
                config = ContinuousResearchConfig.from_dict(json.loads(run["continuous_config_json"] or "{}"), run["provider_type"])
                remote_used = provider_type.upper() == "REMOTE" or str(run["provider_type"]).upper() == "REMOTE" or bool(conn.execute(
                    "SELECT 1 FROM research_model_calls WHERE run_id=? AND provider_type='REMOTE' LIMIT 1", (run_id,)).fetchone())
                if remote_used and str(run["provider_type"]).upper() != "REMOTE":
                    max_calls = min(config.max_model_calls or 100, 100)
                elif remote_used:
                    max_calls = config.max_model_calls or 100
                else:
                    max_calls = config.max_model_calls
                call_count = int(conn.execute("SELECT COUNT(*) FROM research_model_calls WHERE run_id=?", (run_id,)).fetchone()[0])
                if max_calls is not None and call_count >= max_calls:
                    raise ModelProviderError("达到模型调用预算，停止发起新的模型请求", "CALL_BUDGET_EXHAUSTED")
            retry_count = 0
            if cycle_no and agent_id:
                task = conn.execute("SELECT attempt FROM research_tasks WHERE run_id=? AND round_no=? AND agent_id=?", (run_id, cycle_no, agent_id)).fetchone()
                retry_count = max(0, int(task[0]) - 1) if task else 0
            conn.execute("""INSERT INTO research_model_calls(id,run_id,cycle_no,agent_id,purpose,provider_id,provider_type,model_name,
                successful,error_code,retry_count,duration_seconds,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (call_id, run_id, int(cycle_no or 0), agent_id, purpose, provider_id, provider_type, model_name,
                 -1, "", retry_count, 0.0, utc_now()))
        return call_id

    @staticmethod
    def _record_model_call(run_id: Optional[str], cycle_no: Optional[int], agent_id: str, purpose: str,
                           provider_id: str, provider_type: str, model_name: str, successful: bool,
                           error_code: str = "", usage: Any = None, duration_seconds: float = 0.0,
                           call_id: Optional[str] = None):
        if not run_id:
            return
        retry_count = 0
        if cycle_no and agent_id:
            with connection() as conn:
                row = conn.execute("SELECT attempt FROM research_tasks WHERE run_id=? AND round_no=? AND agent_id=?", (run_id, cycle_no, agent_id)).fetchone()
                retry_count = max(0, int(row[0]) - 1) if row else 0
        with connection() as conn:
            if call_id:
                conn.execute("""UPDATE research_model_calls SET successful=?,error_code=?,input_tokens=?,output_tokens=?,total_tokens=?,
                    estimated_cost=?,currency=?,duration_seconds=? WHERE id=?""", (int(successful), error_code,
                    getattr(usage, "input_tokens", None), getattr(usage, "output_tokens", None), getattr(usage, "total_tokens", None),
                    getattr(usage, "estimated_cost", None), getattr(usage, "currency", None), max(0.0, duration_seconds), call_id))
            else:
                conn.execute("""INSERT INTO research_model_calls(id,run_id,cycle_no,agent_id,purpose,provider_id,provider_type,model_name,
                    successful,error_code,retry_count,input_tokens,output_tokens,total_tokens,estimated_cost,currency,duration_seconds,created_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (uuid.uuid4().hex, run_id, int(cycle_no or 0), agent_id, purpose,
                    provider_id, provider_type, model_name, int(successful), error_code, retry_count,
                    getattr(usage, "input_tokens", None), getattr(usage, "output_tokens", None), getattr(usage, "total_tokens", None),
                    getattr(usage, "estimated_cost", None), getattr(usage, "currency", None), max(0.0, duration_seconds), utc_now()))

    @staticmethod
    def _resource_usage(run_id: str, provider_type: str, started_at: str, cycles: int, information_gain: float = 0.0) -> Dict[str, Any]:
        with connection() as conn:
            rows = conn.execute("SELECT * FROM research_model_calls WHERE run_id=? ORDER BY created_at", (run_id,)).fetchall()
            run_runtime = conn.execute("SELECT runtime_seconds_total,status,started_at FROM research_runs WHERE id=?", (run_id,)).fetchone()
        runtime_seconds = float(run_runtime["runtime_seconds_total"] or 0) if run_runtime else 0.0
        if run_runtime and run_runtime["status"] in ("planning", "running", "converging", "pause_requested", "cancel_requested"):
            runtime_start = run_runtime["started_at"] or started_at
            runtime_seconds += max(0, (datetime.now(timezone.utc) - datetime.fromisoformat(runtime_start)).total_seconds())
        known_input = [int(row["input_tokens"]) for row in rows if row["input_tokens"] is not None]
        known_output = [int(row["output_tokens"]) for row in rows if row["output_tokens"] is not None]
        known_total = [int(row["total_tokens"]) for row in rows if row["total_tokens"] is not None]
        cost_groups: Dict[str, float] = {}
        for row in rows:
            if row["estimated_cost"] is not None and row["currency"]:
                currency = str(row["currency"]).upper()
                cost_groups[currency] = cost_groups.get(currency, 0.0) + float(row["estimated_cost"])
        usage = ResearchResourceUsage(runtime_seconds=runtime_seconds,
            cycles=cycles, model_calls=len(rows), successful_calls=sum(1 for row in rows if int(row["successful"]) == 1),
            failed_calls=sum(1 for row in rows if int(row["successful"]) == 0), retry_count=sum(int(row["retry_count"]) for row in rows),
            input_tokens=sum(known_input) if known_input else None, output_tokens=sum(known_output) if known_output else None,
            total_tokens=sum(known_total) if known_total else None,
            estimated_cost=sum(cost_groups.values()) if len(cost_groups) == 1 else None,
            currency=next(iter(cost_groups)) if len(cost_groups) == 1 else None,
            information_gain=information_gain)
        result = usage.to_dict()
        result["provider_type"] = provider_type
        result["research_efficiency"] = usage.research_efficiency(provider_type)
        result["cost_by_currency"] = cost_groups
        result["usage_reported_calls"] = sum(1 for row in rows if row["total_tokens"] is not None)
        result["usage_unreported_calls"] = max(0, len(rows) - result["usage_reported_calls"])
        breakdown: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            key = f"{row['provider_id']}|{row['model_name']}"
            item = breakdown.setdefault(key, {"provider_id": row["provider_id"], "provider_type": row["provider_type"],
                "model_name": row["model_name"], "model_calls": 0, "successful_calls": 0, "failed_calls": 0,
                "input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "estimated_cost": None, "currency": row["currency"]})
            item.setdefault("usage_reported_calls", 0)
            item["model_calls"] += 1
            item["successful_calls"] += int(row["successful"] == 1)
            item["failed_calls"] += int(row["successful"] == 0)
            item["pending_calls"] = item.get("pending_calls", 0) + int(row["successful"] == -1)
            for field in ("input_tokens", "output_tokens", "total_tokens"):
                if row[field] is not None:
                    item[field] += int(row[field])
            if row["total_tokens"] is not None:
                item["usage_reported_calls"] += 1
            if row["estimated_cost"] is not None:
                item["estimated_cost"] = (item["estimated_cost"] or 0.0) + float(row["estimated_cost"])
        for item in breakdown.values():
            if not item["usage_reported_calls"]:
                item["input_tokens"] = item["output_tokens"] = item["total_tokens"] = None
        result["model_breakdown"] = list(breakdown.values())
        result["pricing_available"] = bool(cost_groups)
        return result

    @staticmethod
    def _call_model(system: str, prompt: str, temperature: float = 0.35, max_tokens: int = 2048,
                    run_id: Optional[str] = None, cycle_no: Optional[int] = None,
                    agent_id: str = "", purpose: str = "agent") -> str:
        started = time.perf_counter()
        provider_id, provider_type, model_name = "unknown", "LOCAL", ""
        call_id = None
        try:
            settings = model_settings.get()
            provider_type = "REMOTE" if settings.get("provider") == "remote" else "LOCAL"
            provider_id = (urlparse(str(settings.get("remote_base_url", ""))).hostname or "openai_compatible") if provider_type == "REMOTE" else "ollama"
            model_name = str((settings.get("remote_research_model") or settings.get("remote_chat_model")) if provider_type == "REMOTE" else settings.get("local_research_model", ""))
            provider = model_provider_registry.create_research_provider()
            provider_id, provider_type, model_name = provider.provider_id, provider.provider_type, provider.model
            call_id = ResearchEngine._reserve_model_call(run_id, cycle_no, agent_id, purpose,
                provider_id, provider_type, model_name)
            response = provider.generate(system, prompt, temperature=temperature, max_tokens=max_tokens)
            ResearchEngine._record_model_call(run_id, cycle_no, agent_id, purpose, response.provider_id or provider_id,
                response.provider_type, response.model, True, usage=response.usage, duration_seconds=time.perf_counter() - started,
                call_id=call_id)
            return response.text
        except Exception as exc:
            if isinstance(exc, ModelProviderError):
                error_code = exc.reason_code
            else:
                error_code = "LOCAL_MODEL_ERROR" if provider_type == "LOCAL" else "REMOTE_MODEL_ERROR"
            if not (isinstance(exc, ModelProviderError) and exc.reason_code == "CALL_BUDGET_EXHAUSTED"):
                ResearchEngine._record_model_call(run_id, cycle_no, agent_id, purpose, provider_id, provider_type,
                    model_name, False, error_code=error_code, duration_seconds=time.perf_counter() - started, call_id=call_id)
            raise

    def _agent_prompt(self, agent_id: str, run: sqlite3.Row, round_no: int, sources: str, previous: str) -> str:
        agent = AGENTS[agent_id]
        workflow = WORKFLOWS.get(run["workflow_mode"], WORKFLOWS["multidisciplinary"])
        return (f"研究主题：{run['title']}\n核心问题：{run['question']}\n你的研究职责：{agent.role}\n这是第 {round_no} 轮。\n"
                f"研究模式要求：{workflow.instruction}\n"
                f"本轮资料（W 为联网学术来源，K 为本地知识库）：\n{sources}\n\n"
                f"此前研究进展：\n{previous or '这是第一轮，暂无此前进展。'}\n\n"
                "请从你的专业角度独立分析，提出 3 到 5 条关键发现、支持证据和反证/限制，并提出一个下一轮值得追问的问题。尽量具体、可检验。")

    @staticmethod
    def _synthesis_prompt(title: str, question: str, round_no: int, completed: List[str], review: str = "") -> str:
        # chatai-local is built with num_ctx=4096. Keep synthesis input bounded
        # so the prompt, system message, and generation budget fit that window.
        question = question.strip()
        if len(question) > 700:
            question = question[:520] + "……（问题中段略）……" + question[-150:]
        header = f"研究主题：{title[:160]}\n核心问题：{question}\n第 {round_no} 轮多学科智能体成果：\n"
        footer = "\n\n请综合一致结论、真实分歧、证据强弱、反例和下一轮研究问题。给出清晰摘要；不得把猜想包装成结论。引用沿用智能体使用的 [W#]/[K#] 来源编号。"
        budget = max(0, 2100 - len(header) - len(footer) - len(review) - 8)
        if completed:
            labels = [item.split("：", 1)[0][:80] + "：\n" for item in completed]
            per_item = max(0, (budget - sum(map(len, labels)) - 2 * (len(labels) - 1)) // len(labels))
            excerpts = []
            for item, label in zip(completed, labels):
                body = item.split("\n", 1)[1] if "\n" in item else item
                excerpts.append(label + body[:per_item])
            findings = "\n\n".join(excerpts)
        else:
            findings = "本轮没有智能体输出。"
        return header + findings + footer + review

    def _work(self, run_id: str):
        try:
            initial = self._state(run_id)
            if not initial:
                return
            parallelism = DEPTH_BUDGETS.get(initial["research_depth"], DEPTH_BUDGETS["normal"])["max_parallel_agents"]
            # Keep GPU memory predictable on local 8 GB cards while still
            # running independent agents concurrently.
            with ThreadPoolExecutor(max_workers=parallelism, thread_name_prefix="science-agent") as pool:
                while True:
                    run = self._state(run_id)
                    if not run:
                        return
                    if run["status"] in ("pause_requested", "paused"):
                        self._set_status(run_id, "paused")
                        return
                    if run["status"] in ("cancel_requested", "cancelled"):
                        self._set_status(run_id, "cancelled")
                        return
                    if run["status"] not in ("running", "planning", "converging"):
                        return
                    if run["status"] == "planning":
                        self._set_status(run_id, "running")
                        run = self._state(run_id)
                    if run["max_rounds"] == 0:
                        saved_config = json.loads(run["continuous_config_json"] or "{}")
                        hard_config = ContinuousResearchConfig.from_dict({**saved_config, "auto_sleep_enabled": False})
                        hard_config, effective_provider_type = self._effective_resource_policy(run_id, hard_config, run["provider_type"])
                        elapsed = self._runtime_seconds(run_id) / 60
                        with connection() as conn:
                            recent = [json.loads(row[0]) for row in reversed(conn.execute("SELECT metrics_json FROM research_cycle_metrics WHERE run_id=? ORDER BY cycle_no DESC LIMIT 12", (run_id,)).fetchall())]
                        hard_decision = ContinuousResearchController(hard_config).decide(state=run["status"], elapsed_minutes=elapsed,
                            cycles=int(run["current_round"]), consecutive_errors=int(run["consecutive_errors"]),
                            no_progress_cycles=int(run["no_progress_cycles"]), convergence_cycles=int(run["convergence_cycles"]),
                            recent_metrics=recent, emergency_stop=self._emergency_stop_requested(),
                            provider_type=effective_provider_type,
                            resource_usage=self._resource_usage(run_id, effective_provider_type, run["started_at"] or run["created_at"], int(run["current_round"])),
                            latest_error=self._latest_model_error(run_id))
                        if hard_decision.should_stop:
                            with connection() as conn:
                                conn.execute("UPDATE research_runs SET status=?,error=?,session_summary_json=?,updated_at=? WHERE id=?",
                                    (hard_decision.next_state, hard_decision.reason,
                                     json.dumps(self._session_summary(run, recent, hard_decision.reason), ensure_ascii=False), utc_now(), run_id))
                            return
                    round_no = int(run["current_round"]) + 1
                    if run["max_rounds"] and round_no > run["max_rounds"]:
                        self._set_status(run_id, "completed")
                        return
                    agents = json.loads(run["agents_json"])
                    self._set_task(run_id, round_no, "retrieval", "running")
                    try:
                        local_hits = self.retriever.search(run["question"], top_k=4, owner_id=run["owner_id"])
                    except Exception:
                        local_hits = []
                    web_hits = online_science_search(run["question"], limit=8)
                    sources_text = self._format_sources(web_hits, local_hits)
                    source_records = [{**source, "citation_label": f"W{index}"} for index, source in enumerate(web_hits, 1)] + [{
                        "provider": "本地知识库", "source_type": "theory_internal",
                        "title": str(item.get("metadata", {}).get("filename", "本地知识库资料")),
                        "url": f"local://knowledge-base/{item.get('metadata', {}).get('filename', 'document')}",
                        "snippet": str(item.get("content", ""))[:4000],
                        "citation_label": f"K{index}",
                    } for index, item in enumerate(local_hits, 1)]
                    self._set_task(run_id, round_no, "retrieval", "completed", error="" if source_records else "本轮没有检索到来源")
                    with connection() as conn:
                        conn.execute("UPDATE research_runs SET current_stage='expert_analysis',updated_at=? WHERE id=?", (utc_now(), run_id))
                        conn.commit()
                    with connection() as conn:
                        prior = conn.execute("SELECT round_no,agent_name,status,content FROM research_outputs WHERE run_id=? AND round_no>=? ORDER BY round_no,created_at", (run_id, max(1, round_no - 1))).fetchall()
                    previous = f"上一轮综合摘要：\n{run['summary'][-700:]}\n\n" if run["summary"] else ""
                    previous_session = json.loads(run["session_summary_json"] or "{}")
                    if previous_session:
                        previous += "恢复会话摘要（模型与研究状态分离，以下历史使用情况不限制本次模型选择）：\n"
                        previous += json.dumps({"open_problems": previous_session.get("open_problems", []),
                            "totals": previous_session.get("totals", {}), "reason": previous_session.get("reason", "")}, ensure_ascii=False)[:800] + "\n\n"
                    previous += "\n\n".join(f"第{item['round_no']}轮｜{item['agent_name']}\n{item['content'][:220]}" for item in prior[-6:])
                    related_memories = self._retrieve_memories(run["owner_id"], run_id, round_no, run["question"])
                    if related_memories:
                        previous += "\n\n相关历史研究记忆（均为先前模型生成记录，不可当作证据；应重新核查其中的来源和结论）：\n"
                        previous += "\n\n".join(
                            f"历史问题：{item['question']}\n历史综合：{item['summary'][:500]}\n"
                            f"待追问题：{'；'.join(claim['text'][:140] for claim in item['open_questions'][:3]) or '无结构化待追问题'}"
                            for item in related_memories)
                    previous = previous[-2200:]
                    pending = []
                    for agent_id in agents:
                        saved = self._existing_output(run_id, round_no, agent_id)
                        if saved and saved["status"] == "completed":
                            self._set_task(run_id, round_no, agent_id, "completed", output_id=saved["id"])
                            continue
                        agent = AGENTS[agent_id]
                        system = (agent.system_prompt + "以中文回答，除非用户问题明确使用其他语言。"
                                  "严谨标注证据与推测，只引用给定来源编号，不得编造引用。给出反证与可检验问题。"
                                  "请在正文中用以下固定标签输出可追踪条目，每项独立一行：- [finding] 发现/分析；"
                                  "- [internal] 对内部理论文档的准确转述；- [external] 有外部来源支持的事实（句末标注[W#]）；"
                                  "- [hypothesis] 尚未验证的假设；- [prediction] 可检验预测；"
                                  "- [counterevidence] 反例或反证；- [limitation] 限制或未知。没有来源时不得标成[external]。")
                        self._set_task(run_id, round_no, agent_id, "queued")
                        pending.append((agent_id, pool.submit(self._execute_agent, run_id, round_no, agent_id, system,
                            self._agent_prompt(agent_id, run, round_no, sources_text, previous), agent.temperature, agent.max_tokens)))
                    future_agents = {future: agent_id for agent_id, future in pending}
                    for future in as_completed(future_agents):
                        agent_id = future_agents[future]
                        try:
                            self._save_output(run_id, round_no, agent_id, future.result(), source_records)
                        except Exception as exc:
                            self._save_output(run_id, round_no, agent_id, f"本智能体本轮运行失败：{str(exc)[:500]}", source_records, "failed")
                    run = self._state(run_id)
                    if not run:
                        return
                    if run["status"] in ("pause_requested", "cancel_requested"):
                        self._set_status(run_id, "paused" if run["status"] == "pause_requested" else "cancelled")
                        return
                    completed = []
                    for agent_id in agents:
                        item = self._existing_output(run_id, round_no, agent_id)
                        if item and item["status"] == "completed":
                            completed.append(f"{AGENTS[agent_id].name}：\n{item['content'][:1100]}")
                    if not completed:
                        with connection() as conn:
                            failures = conn.execute("SELECT content FROM research_outputs WHERE run_id=? AND round_no=? AND status='failed' ORDER BY created_at", (run_id, round_no)).fetchall()
                        first_failure = str(failures[0]["content"] if failures else "")
                        if first_failure.startswith("本智能体本轮运行失败："):
                            first_failure = first_failure.removeprefix("本智能体本轮运行失败：")
                        run_error = "本轮没有研究智能体成功完成。"
                        if first_failure:
                            run_error += f"首个模型错误：{first_failure[:420]}"
                        config = ContinuousResearchConfig.from_dict(json.loads(run["continuous_config_json"] or "{}"))
                        next_errors = int(run["consecutive_errors"] or 0) + 1
                        effective_config, effective_provider_type = self._effective_resource_policy(run_id, config, run["provider_type"])
                        decision = ContinuousResearchController(effective_config).decide(state=run["status"],
                            elapsed_minutes=self._runtime_seconds(run_id) / 60,
                            cycles=int(run["current_round"]), consecutive_errors=next_errors,
                            no_progress_cycles=int(run["no_progress_cycles"]), convergence_cycles=int(run["convergence_cycles"]),
                            recent_metrics=[], provider_type=effective_provider_type, latest_error=self._latest_model_error(run_id),
                            resource_usage=self._resource_usage(run_id, effective_provider_type, run["started_at"] or run["created_at"], int(run["current_round"])),
                            emergency_stop=self._emergency_stop_requested()) if run["max_rounds"] == 0 else None
                        terminal = run["max_rounds"] != 0 or bool(decision and (decision.should_stop or decision.should_sleep))
                        final_status = (decision.next_state if decision else "failed") if terminal else "running"
                        final_reason = decision.reason if decision and decision.reason else run_error
                        session_summary = self._session_summary(run, [], final_reason) if terminal and run["max_rounds"] == 0 else {}
                        if decision:
                            session_summary["reason_code"] = decision.reason_code
                        with connection() as conn:
                            conn.execute("UPDATE research_runs SET status=?,consecutive_errors=?,error=?,session_summary_json=?,updated_at=? WHERE id=?",
                                (final_status, next_errors, final_reason if terminal else f"{run_error} 将自动重试（{next_errors}/{config.max_consecutive_errors}）",
                                 json.dumps(session_summary, ensure_ascii=False), utc_now(), run_id))
                            conn.commit()
                        if terminal:
                            return
                        time.sleep(2)
                        continue
                    debate_modes = {"theory_attack", "peer_review", "experiment_design"}
                    debate_limit = DEPTH_BUDGETS.get(run["research_depth"], DEPTH_BUDGETS["normal"])["max_debate_rounds"]
                    if run["workflow_mode"] in debate_modes and debate_limit > 0:
                        debate_output = self._existing_output(run_id, round_no, "debate_critic")
                        if not debate_output or debate_output["status"] != "completed":
                            self._set_task(run_id, round_no, "debate_critic", "queued")
                            self._set_task(run_id, round_no, "debate_critic", "running")
                            critique_prompt = (f"研究问题：{run['question']}\n以下是本轮专家的独立研究：\n\n" + "\n\n".join(completed)
                                + "\n\n请进行交叉质疑：逐条指出最关键的无来源主张、逻辑缺口、反例、已有理论替代解释和可证伪条件。明确区分内部理论文档、外部文献与模型推断；不要为待研究理论辩护。")
                            reviewer = AGENTS["debate_critic"]
                            try:
                                critique = self._call_model(reviewer.system_prompt, critique_prompt, reviewer.temperature, reviewer.max_tokens,
                                    run_id=run_id, cycle_no=round_no, agent_id="debate_critic", purpose="peer_review")
                                self._save_output(run_id, round_no, "debate_critic", critique, source_records)
                                completed.append(f"{reviewer.name}（交叉质疑）：\n{critique[:1400]}")
                            except Exception as exc:
                                self._save_output(run_id, round_no, "debate_critic", f"交叉质疑阶段失败：{str(exc)[:500]}", source_records, "failed")
                        else:
                            completed.append(f"交叉质疑评审员：\n{debate_output['content'][:1400]}")
                        debate_output = self._existing_output(run_id, round_no, "debate_critic")
                        if debate_output and debate_output["status"] == "completed":
                            response_output = self._existing_output(run_id, round_no, "debate_response")
                            if not response_output or response_output["status"] != "completed":
                                self._set_task(run_id, round_no, "debate_response", "queued")
                                responder_id = next((candidate for candidate in agents if candidate not in ("critic", "foundations")), agents[0])
                                responder = AGENTS[responder_id]
                                self._set_task(run_id, round_no, "debate_response", "running")
                                response_prompt = (f"研究问题：{run['question']}\n你代表的专业角色：{responder.name}（{responder.role}）\n"
                                    f"原始专家分析：\n" + "\n\n".join(completed)
                                    + f"\n\n交叉评审意见：\n{debate_output['content'][:2200]}\n\n"
                                    "请逐条回应评审意见，指出哪些成立、哪些需要限定、哪些有来源支持。若证据不足或反例成立，明确修正原主张；不得把理论内部资料当作外部验证。输出简洁、可核查。")
                                response_profile = AGENTS["debate_response"]
                                try:
                                    response_text = self._call_model(responder.system_prompt + response_profile.system_prompt,
                                        response_prompt, response_profile.temperature, response_profile.max_tokens,
                                        run_id=run_id, cycle_no=round_no, agent_id="debate_response", purpose="peer_response")
                                    self._save_output(run_id, round_no, "debate_response", response_text, source_records)
                                    completed.append(f"{response_profile.name}（回应）：\n{response_text[:1200]}")
                                except Exception as exc:
                                    self._save_output(run_id, round_no, "debate_response", f"研究员回应阶段失败：{str(exc)[:500]}", source_records, "failed")
                            else:
                                completed.append(f"研究员回应：\n{response_output['content'][:1200]}")
                    synthesis = self._existing_output(run_id, round_no, "synthesis")
                    if not synthesis or synthesis["status"] != "completed":
                        review_instruction = ("这是有限轮次的质疑审查，请充当研究评审：逐项判断主要批评是已回应、部分回应还是未解决；指出回应是否有可追溯来源。评审提出质疑不等于质疑已成立，研究员回应也不等于主张已证实。若现有资料无法判断，请保留为未解决问题。" if run["workflow_mode"] in debate_modes else "")
                        synthesis_prompt = self._synthesis_prompt(run["title"], run["question"], round_no, completed, review_instruction)
                        try:
                            synthesis_system = (AGENTS["synthesis"].system_prompt + "使用核心问题的语言作答。"
                                "评审中逐条标记接受、部分接受或未解决；不得用投票代替证据判断。"
                                "在报告中将可追踪条目按固定标签独立成行：- [finding] 研究判断；"
                                "- [internal] 内部理论文档的准确转述；- [external] 外部来源支持的事实并标[W#]；"
                                "- [hypothesis] 未验证假设；- [prediction] 可检验预测；"
                                "- [counterevidence] 反例；- [limitation] 未知或限制。")
                            synthesis_agent = AGENTS["synthesis"]
                            summary = self._call_model(synthesis_system, synthesis_prompt, synthesis_agent.temperature, min(synthesis_agent.max_tokens, 800),
                                run_id=run_id, cycle_no=round_no, agent_id="synthesis", purpose="synthesis")
                            self._save_output(run_id, round_no, "synthesis", summary, source_records)
                        except Exception as exc:
                            summary = f"本轮综合失败：{str(exc)[:500]}"
                            self._save_output(run_id, round_no, "synthesis", summary, web_hits, "failed")
                    else:
                        summary = synthesis["content"]
                    with connection() as conn:
                        conn.execute("UPDATE research_runs SET current_round=?,summary=?,updated_at=? WHERE id=?", (round_no, summary, utc_now(), run_id))
                        conn.commit()
                    final_synthesis = self._existing_output(run_id, round_no, "synthesis")
                    if final_synthesis and final_synthesis["status"] == "completed":
                        self._save_memory(run_id, round_no, summary)
                    refreshed = self._state(run_id)
                    if not refreshed or refreshed["status"] == "cancel_requested":
                        self._set_status(run_id, "cancelled")
                        return
                    if refreshed["status"] == "pause_requested":
                        self._set_status(run_id, "paused")
                        return
                    metrics = self._record_cycle_metrics(run_id, round_no, summary,
                        ContinuousResearchConfig.from_dict(json.loads(refreshed["continuous_config_json"] or "{}")))
                    if refreshed["max_rounds"] == 0:
                        next_state = self._apply_continuous_policy(refreshed, metrics)
                        if next_state in ("sleeping", "stopped", "failed"):
                            return
                    refreshed = self._state(run_id)
                    if not refreshed or refreshed["status"] == "cancel_requested":
                        self._set_status(run_id, "cancelled")
                        return
                    if refreshed["status"] == "pause_requested":
                        self._set_status(run_id, "paused")
                        return
                    if refreshed["max_rounds"] and round_no >= refreshed["max_rounds"]:
                        self._set_status(run_id, "completed")
                        return
                    # Avoid an unbounded hot loop when the search/model provider fails.
                    time.sleep(2)
        except Exception as exc:
            ResearchEngine._checkpoint_runtime(run_id)
            with connection() as conn:
                conn.execute("UPDATE research_runs SET status='failed',error=?,updated_at=? WHERE id=?", (str(exc)[:1000], utc_now(), run_id))
                conn.commit()

    @staticmethod
    def _checkpoint_runtime(run_id: str):
        now = datetime.now(timezone.utc)
        with connection() as conn:
            run = conn.execute("SELECT started_at,runtime_seconds_total,status FROM research_runs WHERE id=?", (run_id,)).fetchone()
            if not run or run["status"] not in ("planning", "running", "converging", "pause_requested", "cancel_requested"):
                return
            started_at = run["started_at"]
            if not started_at:
                return
            elapsed = max(0.0, (now - datetime.fromisoformat(started_at)).total_seconds())
            conn.execute("UPDATE research_runs SET runtime_seconds_total=runtime_seconds_total+?,started_at=? WHERE id=?",
                         (elapsed, now.isoformat(), run_id))

    @staticmethod
    def _runtime_seconds(run_id: str) -> float:
        with connection() as conn:
            run = conn.execute("SELECT runtime_seconds_total,started_at,status FROM research_runs WHERE id=?", (run_id,)).fetchone()
        if not run:
            return 0.0
        total = float(run["runtime_seconds_total"] or 0)
        if run["status"] in ("planning", "running", "converging", "pause_requested", "cancel_requested") and run["started_at"]:
            total += max(0, (datetime.now(timezone.utc) - datetime.fromisoformat(run["started_at"])).total_seconds())
        return total

    @staticmethod
    def _set_status(run_id: str, status: str):
        if status not in ("running", "planning", "converging"):
            ResearchEngine._checkpoint_runtime(run_id)
        with connection() as conn:
            conn.execute("UPDATE research_runs SET status=?,updated_at=? WHERE id=?", (status, utc_now(), run_id))
            conn.commit()


def create_research_router(current_user: Callable[..., Dict], rag_store: Any) -> APIRouter:
    router = APIRouter(prefix="/api/research", tags=["science-exploration"])
    engine = ResearchEngine(rag_store)

    @router.get("/agents")
    def research_agents(user: Dict = Depends(current_user)):
        return [profile.to_public_dict() for profile in AGENT_REGISTRY.list_agents()]

    @router.get("/workflows")
    def research_workflows(user: Dict = Depends(current_user)):
        return [{**profile.to_public_dict(), "budgets": DEPTH_BUDGETS} for profile in WORKFLOWS.values()]

    @router.get("/emergency-stop")
    def research_emergency_stop_status(user: Dict = Depends(current_user)):
        return engine.emergency_stop_status()

    @router.post("/emergency-stop")
    def research_emergency_stop(payload: EmergencyStopRequest, user: Dict = Depends(current_user)):
        return engine.set_emergency_stop(payload.enabled)

    @router.post("/route")
    def preview_research_route(payload: PreviewRouteRequest, user: Dict = Depends(current_user)):
        try:
            route = AGENT_ROUTER.route(payload.question, payload.workflow_mode, payload.research_depth, payload.agents)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {
            "agents": [AGENTS[agent_id].to_public_dict() for agent_id in route.agent_ids],
            "workflow_mode": route.workflow_mode,
            "research_depth": route.research_depth,
            "route_reason": route.route_reason,
            "budget": route.budget,
        }

    @router.get("/runs")
    def research_runs(user: Dict = Depends(current_user)):
        return engine.list_runs(user["id"])

    @router.post("/runs", status_code=201)
    def create_research_run(payload: CreateResearchRun, user: Dict = Depends(current_user)):
        return engine.create(payload, user["id"])

    @router.get("/runs/{run_id}")
    def research_run_detail(run_id: str, user: Dict = Depends(current_user)):
        return engine.get_detail(run_id, user["id"])

    @router.get("/runs/{run_id}/graph")
    def research_run_graph(run_id: str, limit_rounds: int = Query(default=50, ge=1, le=500), user: Dict = Depends(current_user)):
        return engine.get_graph(run_id, user["id"], limit_rounds)

    @router.post("/runs/{run_id}/{action}")
    def research_run_control(run_id: str, action: str, user: Dict = Depends(current_user)):
        if action not in ("pause", "resume", "stop"):
            raise HTTPException(404, "操作不存在")
        return engine.control(run_id, user["id"], action)

    return router
