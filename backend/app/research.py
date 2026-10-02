"""Durable multi-agent science exploration tasks."""

import json
import os
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
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field


DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
RESEARCH_DB = DATA_DIR / "research.sqlite"
AGENTS = {
    "physics": {"name": "基础物理研究员", "name_en": "Fundamental Physics", "focus": "物理学、场论、时空、对称性与基础相互作用"},
    "math": {"name": "数学结构研究员", "name_en": "Mathematical Structures", "focus": "数学结构、形式化、公理体系、对称性与可证明性"},
    "complexity": {"name": "复杂系统研究员", "name_en": "Complex Systems", "focus": "复杂系统、非线性动力学、网络、相变与涌现"},
    "cosmology": {"name": "宇宙学研究员", "name_en": "Cosmology", "focus": "宇宙学、早期宇宙、观测证据与主流宇宙模型"},
    "foundations": {"name": "理论基础研究员", "name_en": "Foundations of Science", "focus": "科学哲学、理论统一、概念边界与跨学科对应"},
    "critic": {"name": "反证与审稿研究员", "name_en": "Critical Reviewer", "focus": "寻找反例、证据缺口、概念偷换、可证伪性与替代理论"},
    "synthesis": {"name": "综合研究员", "name_en": "Synthesis Researcher", "focus": "整合各学科发现，建立可检验的综合图景"},
}


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
    agents: List[str] = Field(min_length=2, max_length=8)
    max_rounds: int = Field(default=5, ge=0, le=100)


class ResearchEngine:
    def __init__(self, rag_store: Any):
        self.rags = rag_store
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
        data = self._run_dict(row)
        data["outputs"] = [{**dict(item), "sources": json.loads(item["sources_json"])} for item in outputs]
        return data

    def create(self, payload: CreateResearchRun, owner_id: str) -> Dict[str, Any]:
        agents = list(dict.fromkeys(payload.agents))
        if len(agents) < 2 or any(agent not in AGENTS or agent == "synthesis" for agent in agents):
            raise HTTPException(400, "请选择 2 到 8 个有效的研究智能体")
        run_id, now = uuid.uuid4().hex, utc_now()
        title = payload.title.strip() or payload.question.strip().splitlines()[0][:100]
        with connection() as conn:
            conn.execute("INSERT INTO research_runs(id,owner_id,title,question,agents_json,max_rounds,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)", (run_id, owner_id, title, payload.question.strip(), json.dumps(agents), payload.max_rounds, "running", now, now))
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

    def _save_output(self, run_id: str, round_no: int, agent_id: str, content: str, sources: List[Dict[str, str]], status: str = "completed"):
        with connection() as conn:
            conn.execute("""INSERT INTO research_outputs(id,run_id,round_no,agent_id,agent_name,status,content,sources_json,created_at)
                VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(run_id,round_no,agent_id) DO UPDATE SET
                status=excluded.status,content=excluded.content,sources_json=excluded.sources_json,created_at=excluded.created_at""",
                (uuid.uuid4().hex, run_id, round_no, agent_id, AGENTS[agent_id]["name"], status, content, json.dumps(sources, ensure_ascii=False), utc_now()))
            conn.execute("UPDATE research_runs SET updated_at=? WHERE id=?", (utc_now(), run_id))
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
    def _call_model(system: str, prompt: str) -> str:
        base_url = (os.getenv("OLLAMA_BASE_URL") or "http://127.0.0.1:11434/v1").rstrip("/")
        # Qwen3's default Ollama model is thinking-only; sustained research
        # runs can spend the whole generation budget before producing an answer.
        model = os.getenv("RESEARCH_MODEL") or os.getenv("LOCAL_MODEL") or "chatai-local"
        response = httpx.post(
            f"{base_url}/chat/completions",
            # Qwen3 spends part of its generation budget on hidden reasoning.
            # 850 tokens can be exhausted before it emits `message.content`,
            # making a healthy model look like it returned nothing.
            json={"model": model, "stream": False, "temperature": 0.35, "max_tokens": 2048,
                  "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]},
            headers={"Authorization": "Bearer ollama"}, timeout=600, trust_env=False,
        )
        response.raise_for_status()
        choice = response.json().get("choices", [{}])[0]
        text = choice.get("message", {}).get("content", "").strip()
        if not text:
            if choice.get("finish_reason") == "length":
                raise RuntimeError("模型思考过程耗尽了生成长度，请检查研究输出长度设置")
            raise RuntimeError("本地模型没有返回正文")
        return text

    def _agent_prompt(self, agent_id: str, run: sqlite3.Row, round_no: int, sources: str, previous: str) -> str:
        agent = AGENTS[agent_id]
        return (f"研究主题：{run['title']}\n核心问题：{run['question']}\n这是第 {round_no} 轮。\n"
                f"本轮资料（W 为联网学术来源，K 为本地知识库）：\n{sources}\n\n"
                f"此前研究进展：\n{previous or '这是第一轮，暂无此前进展。'}\n\n"
                "请从你的专业角度独立分析，提出 3 到 5 条关键发现、支持证据和反证/限制，并提出一个下一轮值得追问的问题。尽量具体、可检验。")

    def _work(self, run_id: str):
        try:
            # Keep GPU memory predictable on local 8 GB cards while still
            # running independent agents concurrently.
            with ThreadPoolExecutor(max_workers=2, thread_name_prefix="science-agent") as pool:
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
                    try:
                        local_hits = self.rags.search(run["question"], k=4, owner_id=run["owner_id"])
                    except Exception:
                        local_hits = []
                    web_hits = online_science_search(run["question"], limit=8)
                    sources_text = self._format_sources(web_hits, local_hits)
                    with connection() as conn:
                        prior = conn.execute("SELECT round_no,agent_name,status,content FROM research_outputs WHERE run_id=? AND round_no>=? ORDER BY round_no,created_at", (run_id, max(1, round_no - 1))).fetchall()
                    previous = f"上一轮综合摘要：\n{run['summary'][-700:]}\n\n" if run["summary"] else ""
                    previous += "\n\n".join(f"第{item['round_no']}轮｜{item['agent_name']}\n{item['content'][:220]}" for item in prior[-6:])
                    previous = previous[-2200:]
                    pending = []
                    for agent_id in agents:
                        saved = self._existing_output(run_id, round_no, agent_id)
                        if saved and saved["status"] == "completed":
                            continue
                        agent = AGENTS[agent_id]
                        system = (f"你是{agent['name']}。专业关注：{agent['focus']}。以中文回答，除非用户问题明确使用其他语言。"
                                  "严谨标注证据与推测，只引用给定来源编号，不得编造引用。给出反证与可检验问题。")
                        pending.append((agent_id, pool.submit(self._call_model, system, self._agent_prompt(agent_id, run, round_no, sources_text, previous))))
                    future_agents = {future: agent_id for agent_id, future in pending}
                    for future in as_completed(future_agents):
                        agent_id = future_agents[future]
                        try:
                            self._save_output(run_id, round_no, agent_id, future.result(), web_hits + [{"provider": "本地知识库", "title": item["metadata"]["filename"], "url": "local://knowledge-base"} for item in local_hits])
                        except Exception as exc:
                            self._save_output(run_id, round_no, agent_id, f"本智能体本轮运行失败：{str(exc)[:500]}", web_hits, "failed")
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
                            completed.append(f"{AGENTS[agent_id]['name']}：\n{item['content'][:1100]}")
                    if not completed:
                        with connection() as conn:
                            conn.execute("UPDATE research_runs SET status='failed',error='所有研究智能体均未能完成本轮，请检查 Ollama 后继续。',updated_at=? WHERE id=?", (utc_now(), run_id))
                            conn.commit()
                        return
                    synthesis = self._existing_output(run_id, round_no, "synthesis")
                    if not synthesis:
                        synthesis_prompt = (f"研究主题：{run['title']}\n核心问题：{run['question']}\n第 {round_no} 轮多学科智能体成果：\n"
                                            + ("\n\n".join(completed) or "本轮没有智能体成功返回成果。")
                                            + "\n\n请综合一致结论、真实分歧、证据强弱、反例和下一轮研究问题。给出清晰摘要；不得把猜想包装成结论。引用沿用智能体使用的 [W#]/[K#] 来源编号。")
                        try:
                            synthesis_system = "你是结构生力理论科学探索的综合研究员。保持证据等级清晰，明确报告尚未解决的问题，并使用核心问题的语言作答。"
                            summary = self._call_model(synthesis_system, synthesis_prompt)
                            self._save_output(run_id, round_no, "synthesis", summary, web_hits + [{"provider": "本地知识库", "title": item["metadata"]["filename"], "url": "local://knowledge-base"} for item in local_hits])
                        except Exception as exc:
                            summary = f"本轮综合失败：{str(exc)[:500]}"
                            self._save_output(run_id, round_no, "synthesis", summary, web_hits, "failed")
                    else:
                        summary = synthesis["content"]
                    with connection() as conn:
                        conn.execute("UPDATE research_runs SET current_round=?,summary=?,updated_at=? WHERE id=?", (round_no, summary, utc_now(), run_id))
                        conn.commit()
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
        return [{"id": key, **value} for key, value in AGENTS.items() if key != "synthesis"]

    @router.get("/runs")
    def research_runs(user: Dict = Depends(current_user)):
        return engine.list_runs(user["id"])

    @router.post("/runs", status_code=201)
    def create_research_run(payload: CreateResearchRun, user: Dict = Depends(current_user)):
        return engine.create(payload, user["id"])

    @router.get("/runs/{run_id}")
    def research_run_detail(run_id: str, user: Dict = Depends(current_user)):
        return engine.get_detail(run_id, user["id"])

    @router.post("/runs/{run_id}/{action}")
    def research_run_control(run_id: str, action: str, user: Dict = Depends(current_user)):
        if action not in ("pause", "resume", "stop"):
            raise HTTPException(404, "操作不存在")
        return engine.control(run_id, user["id"], action)

    return router
