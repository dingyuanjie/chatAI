import os
from pathlib import Path
from typing import Optional, Dict

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

load_dotenv()

def get_message_history(session_id: str) -> SQLChatMessageHistory:
    return SQLChatMessageHistory(connection_string=SQLITE_URL, session_id=session_id)


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
            ("system", "你是一个有用的中文助手，会结合对话记忆回答问题。"),
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
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post("/api/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    if not req.session_id or not req.message:
        raise HTTPException(status_code=400, detail="session_id 与 message 均为必填")
    chain = build_chain()
    try:
        reply = chain.invoke({"input": req.message}, config={"configurable": {"session_id": req.session_id}})
    except Exception:
        prompt = ChatPromptTemplate.from_messages(
            [
                ("system", "你是一个有用的中文助手，会结合对话记忆回答问题。"),
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
        reply = fb_with_history.invoke({"input": req.message}, config={"configurable": {"session_id": req.session_id}})
    return ChatResponse(session_id=req.session_id, reply=reply)

@app.get("/api/chat/stream")
def chat_stream(session_id: str, message: str, request: Request):
    if not session_id or not message:
        raise HTTPException(status_code=400, detail="session_id 与 message 均为必填")
    chain = build_chain()

    def event_generator():
        try:
            for chunk in chain.stream({"input": message}, config={"configurable": {"session_id": session_id}}):
                if chunk is None:
                    continue
                yield f"data: {str(chunk)}\n\n"
            yield "event: done\ndata: [DONE]\n\n"
        except Exception as e:
            yield f"event: error\ndata: {str(e)}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


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

