import os
from pathlib import Path
from typing import Optional, Dict, List
import sqlite3
import json

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv
from fastapi.responses import StreamingResponse

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.output_parsers import StrOutputParser
from langchain_core.messages import BaseMessage
from langchain_core.runnables.history import RunnableWithMessageHistory
from langchain_core.runnables import RunnableLambda

from langchain_community.chat_message_histories import SQLChatMessageHistory
from langchain_community.chat_models import ChatTongyi

try:
    from langchain_openai import ChatOpenAI
except Exception:
    ChatOpenAI = None


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

load_dotenv()

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

class SimpleResponder:
    def invoke(self, inputs):
        if isinstance(inputs, list):
            msg = ""
            for m in inputs[::-1]:
                if hasattr(m, "content"):
                    msg = m.content
                    break
            text = msg
        elif isinstance(inputs, dict):
            text = inputs.get("input") or ""
        else:
            text = str(inputs)
        return f"收到：{text}。当前未配置外部模型，返回示例回复。"


def build_chain():
    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", "你是一个有用的中文助手，会结合对话记忆回答问题。\n以下是检索到的知识片段，若相关请参考回答：\n{context}"),
            MessagesPlaceholder(variable_name="history"),
            ("human", "{input}"),
        ]
    )
    model = None
    api_key = os.getenv("OPENAI_API_KEY")
    model_name = os.getenv("MODEL_NAME") or os.getenv("OPENAI_MODEL") or "gpt-4o-mini"
    base_url = os.getenv("OPENAI_API_BASE")
    use_dashscope = base_url and "dashscope" in base_url.lower()
    if use_dashscope and api_key:
        model = ChatTongyi(model=model_name, dashscope_api_key=api_key)
    elif ChatOpenAI and api_key:
        model = ChatOpenAI(model=model_name, api_key=api_key, base_url=base_url, temperature=0.3)
    else:
        model = RunnableLambda(lambda pv: SimpleResponder().invoke(pv.to_messages()))
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
    except Exception:
        prompt = ChatPromptTemplate.from_messages(
            [
                ("system", "你是一个有用的中文助手，会结合对话记忆回答问题。\n以下是检索到的知识片段，若相关请参考回答：\n{context}"),
                MessagesPlaceholder(variable_name="history"),
                ("human", "{input}"),
            ]
        )
        fb_model = RunnableLambda(lambda pv: SimpleResponder().invoke(pv.to_messages()))
        parser = StrOutputParser()
        fb_chain = prompt | fb_model | parser
        fb_with_history = RunnableWithMessageHistory(
            fb_chain,
            get_message_history,
            input_messages_key="input",
            history_messages_key="history",
        )
        reply = fb_with_history.invoke({"input": req.message, "context": context}, config={"configurable": {"session_id": req.session_id}})
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
            for chunk in chain.stream({"input": message, "context": context}, config={"configurable": {"session_id": session_id}}):
                if chunk is None:
                    continue
                yield f"data: {str(chunk)}\n\n"
            yield "event: done\ndata: [DONE]\n\n"
        except Exception as e:
            yield f"event: error\ndata: {str(e)}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")

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

