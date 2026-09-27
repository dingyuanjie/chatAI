import os
from pathlib import Path
from typing import Optional, Dict, List
import sqlite3
import json
import logging
import httpx

from fastapi import FastAPI, HTTPException, Request
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
            conn.commit()
        finally:
            conn.close()

    def add(self, content: str, metadata: Optional[Dict] = None):
        conn = self._conn()
        try:
            cur = conn.cursor()
            meta_str = json.dumps(metadata or {}, ensure_ascii=False)
            cur.execute("INSERT INTO docs(content, metadata) VALUES (?, ?)", (content, meta_str))
            conn.commit()
        finally:
            conn.close()

    def search(self, query: str, k: int = 5) -> List[Dict]:
        conn = self._conn()
        try:
            cur = conn.cursor()
            # 简单 MATCH 查询；若 SQLite 未启用 bm25，则使用默认顺序
            q = (query or "").replace("？", " ").replace("?", " ").replace('"', " ").replace("'", " ")
            if not q.strip():
                return []
            try:
                cur.execute(f'SELECT content, metadata FROM docs WHERE docs MATCH "{q}" LIMIT {int(k)}')
            except sqlite3.Error:
                return []
            rows = cur.fetchall()
            return [{"content": r[0], "metadata": json.loads(r[1] or "{}")} for r in rows]
        finally:
            conn.close()

rags = RAGStore(RAG_DB_PATH)

def build_chain():
    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", "你是一个有用的中文助手，会结合对话记忆回答问题。\n以下是检索到的知识片段，若相关请参考回答：\n{context}"),
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


@app.post("/api/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    if not req.session_id or not req.message:
        raise HTTPException(status_code=400, detail="session_id 与 message 均为必填")
    chain = build_chain()
    docs = rags.search(req.message, k=5)
    context = "\n\n".join([d["content"] for d in docs]) if docs else "（未检索到相关片段）"
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
    docs = rags.search(message, k=5)
    context = "\n\n".join([d["content"] for d in docs]) if docs else "（未检索到相关片段）"

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
