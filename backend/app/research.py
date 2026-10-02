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
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from app.agents.registry import AgentRegistry
from app.agents.router import AgentRouter
from app.agents.workflows import DEPTH_BUDGETS, WORKFLOWS
from app.providers.openai_compatible import OpenAICompatibleChatModel
from app.retrieval.base import RAGStoreAdapter


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
            evidence_columns = {row[1] for row in conn.execute("PRAGMA table_info(research_evidence)").fetchall()}
            if "citation_label" not in evidence_columns:
                conn.execute("ALTER TABLE research_evidence ADD COLUMN citation_label TEXT NOT NULL DEFAULT ''")
            # A process restart is a checkpoint, not a failed or lost run.
            conn.execute("UPDATE research_runs SET status='paused', updated_at=? WHERE status IN ('running','pause_requested','cancel_requested')", (utc_now(),))
            conn.commit()

    @staticmethod
    def _run_dict(row: sqlite3.Row) -> Dict[str, Any]:
        value = dict(row)
        value["agents"] = json.loads(value.pop("agents_json"))
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

    def create(self, payload: CreateResearchRun, owner_id: str) -> Dict[str, Any]:
        try:
            route = AGENT_ROUTER.route(payload.question, payload.workflow_mode, payload.research_depth, payload.agents)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        agents = route.agent_ids
        run_id, now = uuid.uuid4().hex, utc_now()
        title = payload.title.strip() or payload.question.strip().splitlines()[0][:100]
        with connection() as conn:
            conn.execute("""INSERT INTO research_runs(
                id,owner_id,title,question,agents_json,max_rounds,status,workflow_mode,research_depth,route_reason,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""", (run_id, owner_id, title, payload.question.strip(), json.dumps(agents), payload.max_rounds, "running", route.workflow_mode, route.research_depth, route.route_reason, now, now))
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
            row = conn.execute("SELECT status FROM research_runs WHERE id=?", (run_id,)).fetchone()
            status = row["status"]
            if action == "pause" and status in ("running", "queued"):
                conn.execute("UPDATE research_runs SET status='pause_requested', updated_at=? WHERE id=?", (utc_now(), run_id))
            elif action == "stop" and status in ("running", "queued", "pause_requested"):
                conn.execute("UPDATE research_runs SET status='cancel_requested', updated_at=? WHERE id=?", (utc_now(), run_id))
            elif action == "resume" and status in ("paused", "failed", "cancelled"):
                conn.execute("UPDATE research_runs SET status='running', error='', updated_at=? WHERE id=?", (utc_now(), run_id))
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
        return self._call_model(system, prompt, temperature, max_tokens)

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
                (output_id, run_id, round_no, agent_id, AGENTS[agent_id].name, status, content, json.dumps(sources, ensure_ascii=False), now))
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
    def _call_model(system: str, prompt: str, temperature: float = 0.35, max_tokens: int = 2048) -> str:
        # Qwen3 can use part of its generation budget for hidden reasoning;
        # generation limits are profile-specific and handled by the adapter.
        response = OpenAICompatibleChatModel.from_environment().generate(
            system, prompt, temperature=temperature, max_tokens=max_tokens,
        )
        return response.text

    def _agent_prompt(self, agent_id: str, run: sqlite3.Row, round_no: int, sources: str, previous: str) -> str:
        agent = AGENTS[agent_id]
        workflow = WORKFLOWS.get(run["workflow_mode"], WORKFLOWS["multidisciplinary"])
        return (f"研究主题：{run['title']}\n核心问题：{run['question']}\n你的研究职责：{agent.role}\n这是第 {round_no} 轮。\n"
                f"研究模式要求：{workflow.instruction}\n"
                f"本轮资料（W 为联网学术来源，K 为本地知识库）：\n{sources}\n\n"
                f"此前研究进展：\n{previous or '这是第一轮，暂无此前进展。'}\n\n"
                "请从你的专业角度独立分析，提出 3 到 5 条关键发现、支持证据和反证/限制，并提出一个下一轮值得追问的问题。尽量具体、可检验。")

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
                    if run["status"] != "running":
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
                            conn.execute("UPDATE research_runs SET status='failed',error='所有研究智能体均未能完成本轮，请检查 Ollama 后继续。',updated_at=? WHERE id=?", (utc_now(), run_id))
                            conn.commit()
                        return
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
                                critique = self._call_model(reviewer.system_prompt, critique_prompt, reviewer.temperature, reviewer.max_tokens)
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
                                        response_prompt, response_profile.temperature, response_profile.max_tokens)
                                    self._save_output(run_id, round_no, "debate_response", response_text, source_records)
                                    completed.append(f"{response_profile.name}（回应）：\n{response_text[:1200]}")
                                except Exception as exc:
                                    self._save_output(run_id, round_no, "debate_response", f"研究员回应阶段失败：{str(exc)[:500]}", source_records, "failed")
                            else:
                                completed.append(f"研究员回应：\n{response_output['content'][:1200]}")
                    synthesis = self._existing_output(run_id, round_no, "synthesis")
                    if not synthesis:
                        synthesis_prompt = (f"研究主题：{run['title']}\n核心问题：{run['question']}\n第 {round_no} 轮多学科智能体成果：\n"
                                            + ("\n\n".join(completed) or "本轮没有智能体成功返回成果。")
                                            + "\n\n请综合一致结论、真实分歧、证据强弱、反例和下一轮研究问题。给出清晰摘要；不得把猜想包装成结论。引用沿用智能体使用的 [W#]/[K#] 来源编号。"
                                            + ("这是有限轮次的质疑审查，请充当研究评审：逐项判断主要批评是已回应、部分回应还是未解决；指出回应是否有可追溯来源。评审提出质疑不等于质疑已成立，研究员回应也不等于主张已证实。若现有资料无法判断，请保留为未解决问题。" if run["workflow_mode"] in debate_modes else ""))
                        try:
                            synthesis_system = (AGENTS["synthesis"].system_prompt + "使用核心问题的语言作答。"
                                "评审中逐条标记接受、部分接受或未解决；不得用投票代替证据判断。"
                                "在报告中将可追踪条目按固定标签独立成行：- [finding] 研究判断；"
                                "- [internal] 内部理论文档的准确转述；- [external] 外部来源支持的事实并标[W#]；"
                                "- [hypothesis] 未验证假设；- [prediction] 可检验预测；"
                                "- [counterevidence] 反例；- [limitation] 未知或限制。")
                            synthesis_agent = AGENTS["synthesis"]
                            summary = self._call_model(synthesis_system, synthesis_prompt, synthesis_agent.temperature, synthesis_agent.max_tokens)
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
                    if refreshed["max_rounds"] and round_no >= refreshed["max_rounds"]:
                        self._set_status(run_id, "completed")
                        return
                    # Avoid an unbounded hot loop when the search/model provider fails.
                    time.sleep(2)
        except Exception as exc:
            with connection() as conn:
                conn.execute("UPDATE research_runs SET status='failed',error=?,updated_at=? WHERE id=?", (str(exc)[:1000], utc_now(), run_id))
                conn.commit()

    @staticmethod
    def _set_status(run_id: str, status: str):
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
