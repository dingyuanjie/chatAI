import os
from pathlib import Path
from typing import Optional, Dict, List
import sqlite3
import json
import logging
import httpx
import math
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Request, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv
from fastapi.responses import StreamingResponse

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.output_parsers import StrOutputParser
from langchain_core.messages import BaseMessage
from langchain_core.runnables.history import RunnableWithMessageHistory


from langchain_community.chat_message_histories import SQLChatMessageHistory
from langchain_openai import ChatOpenAI

logger = logging.getLogger(__name__)

class ChatRequest(BaseModel):
    session_id: str
    message: str


class ChatResponse(BaseModel):
    session_id: str
    reply: str


DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
SQLITE_URL = f"sqlite:///{(DATA_DIR / 'memory.sqlite').as_posix()}"
RAG_DB_PATH = DATA_DIR / "rag.sqlite"

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
OLLAMA_API_BASE = os.getenv("OLLAMA_API_BASE") or "http://127.0.0.1:11434"
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL") or "qwen3-embedding:0.6b"

def get_message_history(session_id: str) -> SQLChatMessageHistory:
    return SQLChatMessageHistory(connection_string=SQLITE_URL, session_id=session_id)

class RAGStore:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._init_db()

    def _conn(self):
        return sqlite3.connect(self.db_path.as_posix())

    def _init_db(self):
        conn = self._conn()
        try:
            cur = conn.cursor()
            cur.execute("CREATE VIRTUAL TABLE IF NOT EXISTS docs USING fts5(content, metadata)")
            cur.execute("""CREATE TABLE IF NOT EXISTS knowledge_files (
                id TEXT PRIMARY KEY,
                filename TEXT NOT NULL UNIQUE COLLATE NOCASE,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            )""")
            cur.execute("""CREATE TABLE IF NOT EXISTS knowledge_chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_id TEXT NOT NULL REFERENCES knowledge_files(id) ON DELETE CASCADE,
                chunk_index INTEGER NOT NULL,
                content TEXT NOT NULL,
                embedding TEXT NOT NULL
            )""")
            conn.commit()
        finally:
            conn.close()

    CHUNK_SIZE = 800
    CHUNK_OVERLAP = 120

    def _split(self, content: str) -> List[str]:
        text = content.replace("\r\n", "\n").replace("\r", "\n").strip()
        chunks: List[str] = []
        start = 0
        while start < len(text):
            end = min(start + self.CHUNK_SIZE, len(text))
            if end < len(text):
                boundary = text.rfind("\n", start + self.CHUNK_SIZE // 2, end)
                if boundary > start:
                    end = boundary
            chunk = text[start:end].strip()
            if chunk:
                chunks.append(chunk)
            if end >= len(text):
                break
            start = max(start + 1, end - self.CHUNK_OVERLAP)
        return chunks

    def _embed(self, texts: List[str]) -> List[List[float]]:
        try:
            response = httpx.post(
                f"{OLLAMA_API_BASE.rstrip('/')}/api/embed",
                json={"model": EMBEDDING_MODEL, "input": texts, "truncate": True},
                timeout=180,
                trust_env=False,
            )
            response.raise_for_status()
            vectors = response.json().get("embeddings", [])
            if len(vectors) != len(texts) or any(not vector for vector in vectors):
                raise ValueError("Embedding service returned incomplete vectors")
            return vectors
        except Exception as exc:
            logger.exception("Local embedding request failed")
            raise HTTPException(503, f"本地向量模型不可用，请运行 ollama pull {EMBEDDING_MODEL}。") from exc

    def list_files(self) -> List[Dict]:
        with self._conn() as conn:
            rows = conn.execute("""SELECT f.id, f.filename, f.created_at, COUNT(c.id)
                FROM knowledge_files f LEFT JOIN knowledge_chunks c ON c.file_id=f.id
                GROUP BY f.id ORDER BY f.created_at DESC""").fetchall()
        return [{"id": row[0], "filename": row[1], "created_at": row[2], "chunks": row[3]} for row in rows]

    def get_file(self, file_id: str) -> Optional[Dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT id, filename, content, created_at FROM knowledge_files WHERE id=?", (file_id,)
            ).fetchone()
        return {"id": row[0], "filename": row[1], "content": row[2], "created_at": row[3]} if row else None

    def upsert_file(self, filename: str, content: str) -> Dict:
        chunks = self._split(content)
        vectors = self._embed([f"Passage: {chunk}" for chunk in chunks])
        created_at = datetime.now(timezone.utc).isoformat()
        file_id = os.urandom(16).hex()
        with self._conn() as conn:
            conn.execute("PRAGMA foreign_keys=ON")
            old = conn.execute(
                "SELECT id FROM knowledge_files WHERE filename=? COLLATE NOCASE", (filename,)
            ).fetchone()
            if old:
                file_id = old[0]
                conn.execute("DELETE FROM docs WHERE json_extract(metadata, '$.file_id')=?", (file_id,))
                conn.execute("DELETE FROM knowledge_chunks WHERE file_id=?", (file_id,))
                conn.execute("UPDATE knowledge_files SET content=?, created_at=? WHERE id=?", (content, created_at, file_id))
            else:
                conn.execute(
                    "INSERT INTO knowledge_files(id, filename, content, created_at) VALUES (?, ?, ?, ?)",
                    (file_id, filename, content, created_at),
                )
            for index, (chunk, vector) in enumerate(zip(chunks, vectors)):
                conn.execute(
                    "INSERT INTO knowledge_chunks(file_id, chunk_index, content, embedding) VALUES (?, ?, ?, ?)",
                    (file_id, index, chunk, json.dumps(vector)),
                )
                conn.execute(
                    "INSERT INTO docs(content, metadata) VALUES (?, ?)",
                    (chunk, json.dumps({"file_id": file_id, "filename": filename}, ensure_ascii=False)),
                )
        return {"id": file_id, "filename": filename, "chunks": len(chunks)}

    def delete_file(self, file_id: str) -> bool:
        with self._conn() as conn:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("DELETE FROM docs WHERE json_extract(metadata, '$.file_id')=?", (file_id,))
            cursor = conn.execute("DELETE FROM knowledge_files WHERE id=?", (file_id,))
        return cursor.rowcount > 0

    def add(self, content: str, metadata: Optional[Dict] = None):
        return self.upsert_file((metadata or {}).get("filename", "手动录入.md"), content)

    @staticmethod
    def _cosine(left: List[float], right: List[float]) -> float:
        denominator = math.sqrt(sum(x * x for x in left) * sum(y * y for y in right))
        return sum(x * y for x, y in zip(left, right)) / denominator if denominator else 0.0

    def search(self, query: str, k: int = 5) -> List[Dict]:
        text = (query or "").strip()
        if not text:
            return []
        with self._conn() as conn:
            if not conn.execute("SELECT 1 FROM knowledge_chunks LIMIT 1").fetchone():
                return []
        query_vector = self._embed([
            f"Instruct: Retrieve relevant passages that answer the query\nQuery: {text}"
        ])[0]
        with self._conn() as conn:
            rows = conn.execute("""SELECT c.content, c.embedding, f.filename
                FROM knowledge_chunks c JOIN knowledge_files f ON f.id=c.file_id""").fetchall()
        ranked = [
            {"content": row[0], "metadata": {"filename": row[2]}, "score": self._cosine(query_vector, json.loads(row[1]))}
            for row in rows
        ]
        return sorted(ranked, key=lambda item: item["score"], reverse=True)[:max(0, min(k, 20))]

rags = RAGStore(RAG_DB_PATH)

def build_chain():
    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", "你是中文知识库助手，会结合对话记忆回答。回答知识库问题时，优先依据资料片段，用 [文件名] 标出依据；资料没有答案时，明确说知识库中没有找到，不得编造。\n资料片段：\n{context}"),
            MessagesPlaceholder(variable_name="history"),
            ("human", "{input}"),
        ]
    )
    model = ChatOpenAI(
        model=os.getenv("LOCAL_MODEL") or "chatai-local",
        api_key="ollama",
        base_url=os.getenv("OLLAMA_BASE_URL") or "http://127.0.0.1:11434/v1",
        temperature=0.3,
        max_tokens=4096,
        timeout=180,
        max_retries=0,
        http_client=httpx.Client(trust_env=False, timeout=180),
    )
    parser = StrOutputParser()
    chain = prompt | model | parser
    with_history = RunnableWithMessageHistory(
        chain,
        get_message_history,
        input_messages_key="input",
        history_messages_key="history",
    )
    return with_history


app = FastAPI(title="ChatBot with Memory")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173"],
    allow_origin_regex=r"http://localhost:\d+",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/rag/files")
def rag_list_files():
    return rags.list_files()


@app.post("/api/rag/files", status_code=201)
async def rag_upload_file(file: UploadFile = File(...)):
    filename = (file.filename or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not filename or not filename.lower().endswith(".md"):
        raise HTTPException(status_code=400, detail="仅支持上传 .md Markdown 文件")
    raw = await file.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Markdown 文件不能超过 2 MB")
    try:
        content = raw.decode("utf-8-sig").strip()
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail="文件必须使用 UTF-8 编码") from exc
    if not content:
        raise HTTPException(status_code=400, detail="Markdown 文件内容为空")
    return rags.upsert_file(filename, content)


@app.get("/api/rag/files/{file_id}")
def rag_preview_file(file_id: str):
    document = rags.get_file(file_id)
    if not document:
        raise HTTPException(status_code=404, detail="文件不存在")
    return document


@app.delete("/api/rag/files/{file_id}")
def rag_delete_file(file_id: str):
    if not rags.delete_file(file_id):
        raise HTTPException(status_code=404, detail="文件不存在")
    return {"ok": True}


@app.post("/api/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    if not req.session_id or not req.message:
        raise HTTPException(status_code=400, detail="session_id 与 message 均为必填")
    chain = build_chain()
    docs = rags.search(req.message, k=4)
    context = "\n\n".join(
        f"[资料：{d['metadata']['filename']}]\n{d['content']}" for d in docs
    ) if docs else "（知识库暂无可检索资料）"
    try:
        reply = chain.invoke({"input": req.message, "context": context}, config={"configurable": {"session_id": req.session_id}})
    except Exception as exc:
        logger.exception("Local chat completion failed")
        raise HTTPException(status_code=503, detail="本地模型调用失败，请确认 Ollama 已启动并运行 setup-local-model.ps1 完成模型准备。") from exc
    return ChatResponse(session_id=req.session_id, reply=reply)

@app.get("/api/chat/stream")
def chat_stream(session_id: str, message: str, request: Request):
    if not session_id or not message:
        raise HTTPException(status_code=400, detail="session_id 与 message 均为必填")
    chain = build_chain()
    docs = rags.search(message, k=4)
    context = "\n\n".join(
        f"[资料：{d['metadata']['filename']}]\n{d['content']}" for d in docs
    ) if docs else "（知识库暂无可检索资料）"

    def event_generator():
        try:
            yield ": connected\n\n"
            has_content = False
            for chunk in chain.stream({"input": message, "context": context}, config={"configurable": {"session_id": session_id}}):
                if not chunk:
                    continue
                has_content = True
                # Each line needs its own SSE data prefix, including blank lines.
                yield "".join(f"data: {line}\n" for line in str(chunk).replace("\r\n", "\n").split("\n")) + "\n"
            if has_content:
                yield "event: done\ndata: [DONE]\n\n"
            else:
                yield "event: error\ndata: 模型未生成正文，请缩短问题后重试。\n\n"
        except Exception as e:
            logger.exception("Local streaming completion failed")
            detail = "本地模型回复失败，请检查 Ollama 服务后重试。"
            yield f"event: error\ndata: {detail}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

class IngestRequest(BaseModel):
    content: str
    metadata: Optional[Dict] = None

@app.post("/api/rag/ingest")
def rag_ingest(req: IngestRequest):
    if not req.content or len(req.content.strip()) == 0:
        raise HTTPException(status_code=400, detail="content 必填")
    rags.add(req.content.strip(), req.metadata or {})
    return {"ok": True}

@app.get("/api/rag/search")
def rag_search(q: str, k: int = 5):
    items = rags.search(q, k)
    return items

@app.get("/api/history/{session_id}")
def get_history(session_id: str):
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id 必填")
    history = get_message_history(session_id)
    messages: Dict[str, str] = []
    try:
        msgs = history.get_messages()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return [
        {"role": m.type if hasattr(m, "type") else "assistant", "content": m.content}
        for m in msgs
    ]


@app.delete("/api/history/{session_id}")
def clear_history(session_id: str):
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id 必填")
    history = get_message_history(session_id)
    try:
        history.clear()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"ok": True}
