import os
from pathlib import Path
from typing import Optional, Dict, List, Any
import sqlite3
import json
import logging
import httpx
import math
import hashlib
import hmac
import secrets
import re
import time
import difflib
from contextlib import contextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Response, Depends
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
AUTH_DB_PATH = DATA_DIR / "auth.sqlite"
AUTH_COOKIE = "chatai_session"
AUTH_MAX_AGE = 60 * 60 * 24 * 30

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
from app.model_settings import model_settings

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
                owner_id TEXT NOT NULL,
                filename TEXT NOT NULL COLLATE NOCASE,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(owner_id, filename)
            )""")
            cur.execute("""CREATE TABLE IF NOT EXISTS knowledge_chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_id TEXT NOT NULL REFERENCES knowledge_files(id) ON DELETE CASCADE,
                chunk_index INTEGER NOT NULL,
                content TEXT NOT NULL,
                embedding TEXT NOT NULL,
                embedding_model TEXT NOT NULL DEFAULT 'qwen3-embedding:0.6b'
            )""")
            chunk_columns = {row[1] for row in cur.execute("PRAGMA table_info(knowledge_chunks)").fetchall()}
            if "embedding_model" not in chunk_columns:
                cur.execute("ALTER TABLE knowledge_chunks ADD COLUMN embedding_model TEXT NOT NULL DEFAULT 'qwen3-embedding:0.6b'")
            columns = {row[1] for row in cur.execute("PRAGMA table_info(knowledge_files)").fetchall()}
            if "owner_id" not in columns:
                cur.execute("ALTER TABLE knowledge_chunks RENAME TO knowledge_chunks_legacy")
                cur.execute("ALTER TABLE knowledge_files RENAME TO knowledge_files_legacy")
                cur.execute("""CREATE TABLE knowledge_files (
                    id TEXT PRIMARY KEY, owner_id TEXT NOT NULL,
                    filename TEXT NOT NULL COLLATE NOCASE, content TEXT NOT NULL,
                    created_at TEXT NOT NULL, UNIQUE(owner_id, filename)
                )""")
                cur.execute("""CREATE TABLE knowledge_chunks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_id TEXT NOT NULL REFERENCES knowledge_files(id) ON DELETE CASCADE,
                    chunk_index INTEGER NOT NULL, content TEXT NOT NULL, embedding TEXT NOT NULL,
                    embedding_model TEXT NOT NULL DEFAULT 'qwen3-embedding:0.6b'
                )""")
                cur.execute("INSERT INTO knowledge_files(id,owner_id,filename,content,created_at) SELECT id,'legacy',filename,content,created_at FROM knowledge_files_legacy")
                cur.execute("INSERT INTO knowledge_chunks(file_id,chunk_index,content,embedding) SELECT file_id,chunk_index,content,embedding FROM knowledge_chunks_legacy")
                cur.execute("DROP TABLE knowledge_chunks_legacy")
                cur.execute("DROP TABLE knowledge_files_legacy")
            cur.execute("""CREATE TABLE IF NOT EXISTS knowledge_file_versions (
                id TEXT PRIMARY KEY,
                file_id TEXT NOT NULL REFERENCES knowledge_files(id) ON DELETE CASCADE,
                version_no INTEGER NOT NULL,
                content_hash TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(file_id, version_no), UNIQUE(file_id, content_hash)
            )""")
            existing = cur.execute("""SELECT f.id, f.content, f.created_at FROM knowledge_files f
                LEFT JOIN knowledge_file_versions v ON v.file_id=f.id WHERE v.id IS NULL""").fetchall()
            for file_id, content, created_at in existing:
                cur.execute("INSERT OR IGNORE INTO knowledge_file_versions(id,file_id,version_no,content_hash,content,created_at) VALUES(?,?,?,?,?,?)",
                    (os.urandom(16).hex(), file_id, 1, hashlib.sha256(content.encode('utf-8')).hexdigest(), content, created_at))
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

    def _embed(self, texts: List[str], model_name: Optional[str] = None, ollama_url: Optional[str] = None) -> List[List[float]]:
        settings = model_settings.get()
        embedding_model = model_name or settings["embedding_model"]
        embedding_url = ollama_url or settings["ollama_url"]
        try:
            response = httpx.post(
                f"{embedding_url.rstrip('/')}/api/embed",
                json={"model": embedding_model, "input": texts, "truncate": True},
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
            raise HTTPException(503, f"本地向量模型 {embedding_model} 不可用，请检查 Ollama 地址并确认该模型已安装。") from exc

    def reindex_embeddings(self, model_name: str, ollama_url: Optional[str] = None) -> int:
        with self._conn() as conn:
            rows = conn.execute("SELECT id,content FROM knowledge_chunks ORDER BY id").fetchall()
        updates = []
        for start in range(0, len(rows), 16):
            batch = rows[start:start + 16]
            vectors = self._embed([f"Passage: {row[1]}" for row in batch], model_name=model_name, ollama_url=ollama_url)
            updates.extend((json.dumps(vector), model_name, row[0]) for row, vector in zip(batch, vectors))
        with self._conn() as conn:
            conn.executemany("UPDATE knowledge_chunks SET embedding=?,embedding_model=? WHERE id=?", updates)
        return len(updates)

    def list_files(self, owner_id: str) -> List[Dict]:
        with self._conn() as conn:
            rows = conn.execute("""SELECT f.id, f.filename, f.created_at, COUNT(DISTINCT c.id), COALESCE(MAX(v.version_no),1)
                FROM knowledge_files f LEFT JOIN knowledge_chunks c ON c.file_id=f.id
                LEFT JOIN knowledge_file_versions v ON v.file_id=f.id
                WHERE f.owner_id=? GROUP BY f.id ORDER BY f.created_at DESC""", (owner_id,)).fetchall()
        return [{"id": row[0], "filename": row[1], "created_at": row[2], "chunks": row[3], "version": row[4]} for row in rows]

    def claim_legacy_files(self, owner_id: str):
        with self._conn() as conn:
            conn.execute("UPDATE knowledge_files SET owner_id=? WHERE owner_id='legacy'", (owner_id,))

    def get_file(self, file_id: str, owner_id: str) -> Optional[Dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT id, filename, content, created_at FROM knowledge_files WHERE id=? AND owner_id=?", (file_id, owner_id)
            ).fetchone()
            version = conn.execute("SELECT COALESCE(MAX(version_no),1) FROM knowledge_file_versions WHERE file_id=?", (file_id,)).fetchone()[0] if row else None
        return {"id": row[0], "filename": row[1], "content": row[2], "created_at": row[3], "version": version} if row else None

    def upsert_file(self, filename: str, content: str, owner_id: str) -> Dict:
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        with self._conn() as conn:
            old = conn.execute("SELECT id, content FROM knowledge_files WHERE filename=? COLLATE NOCASE AND owner_id=?", (filename, owner_id)).fetchone()
            if old and hashlib.sha256(old[1].encode("utf-8")).hexdigest() == content_hash:
                version = conn.execute("SELECT COALESCE(MAX(version_no),1) FROM knowledge_file_versions WHERE file_id=?", (old[0],)).fetchone()[0]
                count = conn.execute("SELECT COUNT(*) FROM knowledge_chunks WHERE file_id=?", (old[0],)).fetchone()[0]
                return {"id": old[0], "filename": filename, "chunks": count, "version": version, "unchanged": True}
        chunks = self._split(content)
        embedding_model = model_settings.get()["embedding_model"]
        vectors = self._embed([f"Passage: {chunk}" for chunk in chunks])
        created_at = datetime.now(timezone.utc).isoformat()
        file_id = os.urandom(16).hex()
        with self._conn() as conn:
            conn.execute("PRAGMA foreign_keys=ON")
            old = conn.execute(
                "SELECT id FROM knowledge_files WHERE filename=? COLLATE NOCASE AND owner_id=?", (filename, owner_id)
            ).fetchone()
            if old:
                file_id = old[0]
                version = conn.execute("SELECT COALESCE(MAX(version_no),0)+1 FROM knowledge_file_versions WHERE file_id=?", (file_id,)).fetchone()[0]
                conn.execute("DELETE FROM docs WHERE json_extract(metadata, '$.file_id')=?", (file_id,))
                conn.execute("DELETE FROM knowledge_chunks WHERE file_id=?", (file_id,))
                conn.execute("UPDATE knowledge_files SET content=?, created_at=? WHERE id=?", (content, created_at, file_id))
            else:
                version = 1
                conn.execute(
                    "INSERT INTO knowledge_files(id, owner_id, filename, content, created_at) VALUES (?, ?, ?, ?, ?)",
                    (file_id, owner_id, filename, content, created_at),
                )
            conn.execute("INSERT INTO knowledge_file_versions(id,file_id,version_no,content_hash,content,created_at) VALUES(?,?,?,?,?,?)",
                (os.urandom(16).hex(), file_id, version, content_hash, content, created_at))
            for index, (chunk, vector) in enumerate(zip(chunks, vectors)):
                conn.execute(
                    "INSERT INTO knowledge_chunks(file_id, chunk_index, content, embedding, embedding_model) VALUES (?, ?, ?, ?, ?)",
                    (file_id, index, chunk, json.dumps(vector), embedding_model),
                )
                conn.execute(
                    "INSERT INTO docs(content, metadata) VALUES (?, ?)",
                    (chunk, json.dumps({"file_id": file_id, "filename": filename}, ensure_ascii=False)),
                )
        return {"id": file_id, "filename": filename, "chunks": len(chunks), "version": version, "unchanged": False}

    def list_versions(self, file_id: str, owner_id: str) -> Optional[List[Dict]]:
        with self._conn() as conn:
            if not conn.execute("SELECT 1 FROM knowledge_files WHERE id=? AND owner_id=?", (file_id, owner_id)).fetchone():
                return None
            rows = conn.execute("SELECT version_no,content_hash,length(content),created_at FROM knowledge_file_versions WHERE file_id=? ORDER BY version_no DESC", (file_id,)).fetchall()
        return [{"version":r[0],"content_hash":r[1],"characters":r[2],"created_at":r[3]} for r in rows]

    def get_version(self, file_id: str, version_no: int, owner_id: str) -> Optional[Dict]:
        with self._conn() as conn:
            row = conn.execute("SELECT v.version_no,v.content,v.created_at,f.filename FROM knowledge_file_versions v JOIN knowledge_files f ON f.id=v.file_id WHERE v.file_id=? AND v.version_no=? AND f.owner_id=?", (file_id,version_no,owner_id)).fetchone()
        return {"version":row[0],"content":row[1],"created_at":row[2],"filename":row[3]} if row else None

    def compare_versions(self, file_id: str, from_version: int, to_version: int, owner_id: str) -> Optional[Dict]:
        before = self.get_version(file_id, from_version, owner_id)
        after = self.get_version(file_id, to_version, owner_id)
        if not before or not after:
            return None
        diff = "".join(difflib.unified_diff(before["content"].splitlines(True), after["content"].splitlines(True), fromfile=f"v{from_version}", tofile=f"v{to_version}"))
        return {"from_version":from_version,"to_version":to_version,"diff":diff}

    def delete_file(self, file_id: str, owner_id: str) -> bool:
        with self._conn() as conn:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("DELETE FROM docs WHERE json_extract(metadata, '$.file_id')=?", (file_id,))
            cursor = conn.execute("DELETE FROM knowledge_files WHERE id=? AND owner_id=?", (file_id, owner_id))
        return cursor.rowcount > 0

    def add(self, content: str, owner_id: str, metadata: Optional[Dict] = None):
        return self.upsert_file((metadata or {}).get("filename", "手动录入.md"), content, owner_id)

    @staticmethod
    def _cosine(left: List[float], right: List[float]) -> float:
        denominator = math.sqrt(sum(x * x for x in left) * sum(y * y for y in right))
        return sum(x * y for x, y in zip(left, right)) / denominator if denominator else 0.0

    def search(self, query: str, k: int = 5, owner_id: str = "legacy") -> List[Dict]:
        text = (query or "").strip()
        if not text:
            return []
        embedding_model = model_settings.get()["embedding_model"]
        with self._conn() as conn:
            if not conn.execute("SELECT 1 FROM knowledge_chunks c JOIN knowledge_files f ON f.id=c.file_id WHERE f.owner_id=? LIMIT 1", (owner_id,)).fetchone():
                return []
            stale = conn.execute("SELECT 1 FROM knowledge_chunks c JOIN knowledge_files f ON f.id=c.file_id WHERE f.owner_id=? AND c.embedding_model<>? LIMIT 1", (owner_id, embedding_model)).fetchone()
        if stale:
            raise HTTPException(409, "本地向量模型已切换，知识库索引正在重建或需要重新索引，请稍后重试。")
        query_vector = self._embed([
            f"Instruct: Retrieve relevant passages that answer the query\nQuery: {text}"
        ])[0]
        with self._conn() as conn:
            rows = conn.execute("""SELECT c.content, c.embedding, f.filename
                FROM knowledge_chunks c JOIN knowledge_files f ON f.id=c.file_id WHERE f.owner_id=?""", (owner_id,)).fetchall()
        ranked = [
            {"content": row[0], "metadata": {"filename": row[2]}, "score": self._cosine(query_vector, json.loads(row[1]))}
            for row in rows
        ]
        return sorted(ranked, key=lambda item: item["score"], reverse=True)[:max(0, min(k, 20))]

rags = RAGStore(RAG_DB_PATH)
app = FastAPI(title="Structural Vital Force Theory Research Workspace")


@contextmanager
def _auth_conn():
    conn = sqlite3.connect(AUTH_DB_PATH.as_posix())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


with _auth_conn() as conn:
    conn.execute("CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE COLLATE NOCASE, display_name TEXT NOT NULL, password_hash TEXT NOT NULL, created_at TEXT NOT NULL)")
    conn.execute("CREATE TABLE IF NOT EXISTS auth_sessions (token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE, expires_at INTEGER NOT NULL)")
    conn.execute("CREATE TABLE IF NOT EXISTS chat_sessions (session_id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE, title TEXT NOT NULL DEFAULT '新对话', updated_at TEXT NOT NULL)")


def _hash_password(password: str, salt: Optional[bytes] = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 310_000)
    return f"{salt.hex()}${digest.hex()}"


def _password_matches(password: str, stored: str) -> bool:
    try:
        salt_hex, digest_hex = stored.split("$", 1)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), 310_000).hex()
        return hmac.compare_digest(actual, digest_hex)
    except (ValueError, TypeError):
        return False


def _public_user(user: sqlite3.Row) -> Dict:
    return {"id": user["id"], "email": user["email"], "name": user["display_name"]}


def current_user(request: Request) -> Dict:
    token = request.cookies.get(AUTH_COOKIE)
    if not token:
        raise HTTPException(401, "请先登录")
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    with _auth_conn() as conn:
        row = conn.execute("SELECT u.id, u.email, u.display_name FROM auth_sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires_at>?", (token_hash, int(time.time()))).fetchone()
    if not row:
        raise HTTPException(401, "登录已过期，请重新登录")
    return _public_user(row)


def require_owned_session(session_id: str, user: Dict):
    with _auth_conn() as conn:
        row = conn.execute("SELECT 1 FROM chat_sessions WHERE session_id=? AND user_id=?", (session_id, user["id"])).fetchone()
    if not row:
        raise HTTPException(404, "会话不存在")


def _issue_session(user_id: str, response: Response):
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    with _auth_conn() as conn:
        conn.execute("INSERT INTO auth_sessions(token_hash,user_id,expires_at) VALUES(?,?,?)", (token_hash, user_id, int(time.time()) + AUTH_MAX_AGE))
    response.set_cookie(AUTH_COOKIE, token, max_age=AUTH_MAX_AGE, httponly=True, samesite="lax", secure=False, path="/")


class AuthRequest(BaseModel):
    email: str
    password: str
    name: Optional[str] = None


class SessionCreateRequest(BaseModel):
    session_id: str
    title: Optional[str] = None


@app.post("/api/auth/register")
def register(payload: AuthRequest, response: Response):
    email = payload.email.strip().lower()
    password = payload.password
    name = (payload.name or email.split("@", 1)[0]).strip()[:48]
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        raise HTTPException(400, "请输入有效的邮箱地址")
    if len(password) < 8 or len(password) > 256:
        raise HTTPException(400, "密码长度需要在 8 到 256 个字符之间")
    user_id = secrets.token_hex(16)
    try:
        with _auth_conn() as conn:
            conn.execute("INSERT INTO users(id,email,display_name,password_hash,created_at) VALUES(?,?,?,?,?)", (user_id, email, name or "新用户", _hash_password(password), datetime.now(timezone.utc).isoformat()))
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, "该邮箱已注册，请直接登录") from exc
    rags.claim_legacy_files(user_id)
    _issue_session(user_id, response)
    return {"id": user_id, "email": email, "name": name or "新用户"}


@app.post("/api/auth/login")
def login(payload: AuthRequest, response: Response):
    email = payload.email.strip().lower()
    with _auth_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE email=? COLLATE NOCASE", (email,)).fetchone()
    if not row or not _password_matches(payload.password, row["password_hash"]):
        raise HTTPException(401, "邮箱或密码不正确")
    _issue_session(row["id"], response)
    return _public_user(row)


@app.get("/api/auth/me")
def auth_me(user: Dict = Depends(current_user)):
    return user


@app.get("/api/settings/models")
def get_model_settings(user: Dict = Depends(current_user)):
    return model_settings.public()


@app.get("/api/settings/models/available")
def available_models(user: Dict = Depends(current_user)):
    settings = model_settings.get()
    try:
        response = httpx.get(f"{settings['ollama_url']}/api/tags", timeout=8, trust_env=False)
        response.raise_for_status()
        local = [item.get("name", "") for item in response.json().get("models", []) if item.get("name")]
    except Exception as exc:
        local = []
        local_error = str(exc)[:240]
    else:
        local_error = ""
    remote: List[str] = []
    remote_error = ""
    if settings["provider"] == "remote":
        try:
            response = httpx.get(f"{settings['remote_base_url'].rstrip('/')}/models", headers={"Authorization": f"Bearer {settings['remote_api_key']}"} if settings["remote_api_key"] else {}, timeout=8, trust_env=False)
            response.raise_for_status()
            remote = [item.get("id", "") for item in response.json().get("data", []) if item.get("id")]
        except Exception as exc:
            remote_error = str(exc)[:240]
    return {"local": local, "remote": remote, "local_error": local_error, "remote_error": remote_error}


@app.put("/api/settings/models")
def update_model_settings(payload: Dict[str, Any], user: Dict = Depends(current_user)):
    before = model_settings.get()
    requested_embedding = str(payload.get("embedding_model", before["embedding_model"])).strip()
    try:
        validated = model_settings.validate_update(payload)
        requested_ollama_url = validated.get("ollama_url", before["ollama_url"])
        if requested_embedding and (requested_embedding != before["embedding_model"] or requested_ollama_url != before["ollama_url"]):
            count = rags.reindex_embeddings(requested_embedding, ollama_url=requested_ollama_url)
        else:
            count = 0
        model_settings.update(payload)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Could not update global model settings")
        raise HTTPException(503, f"设置未保存：{exc}") from exc
    result = model_settings.public()
    result["reindexed_chunks"] = count
    return result


@app.post("/api/settings/models/test")
def test_model_settings(payload: Optional[Dict[str, Any]] = None, user: Dict = Depends(current_user)):
    settings = model_settings.get()
    try:
        if payload:
            settings.update(model_settings.validate_update(payload))
        response = httpx.get(f"{settings['ollama_url']}/api/tags", timeout=8, trust_env=False)
        response.raise_for_status()
        installed = {item.get("name") for item in response.json().get("models", [])}
        installed.update(name[:-len(":latest")] for name in tuple(installed) if isinstance(name, str) and name.endswith(":latest"))
        if settings["embedding_model"] not in installed:
            raise HTTPException(400, f"本地向量模型未安装：{settings['embedding_model']}")
        if settings["provider"] == "local":
            missing = [name for name in (settings["local_chat_model"], settings["local_research_model"], settings["embedding_model"]) if name and name not in installed]
            if missing:
                raise HTTPException(400, f"Ollama 已连接，但未安装这些模型：{', '.join(missing)}")
            return {"ok": True, "message": "Ollama 已连接，聊天、科研和向量模型均已安装。"}
        if not settings["remote_chat_model"]:
            raise HTTPException(400, "请先填写远端聊天模型名称")
        response = httpx.get(f"{settings['remote_base_url'].rstrip('/')}/models", headers={"Authorization": f"Bearer {settings['remote_api_key']}"} if settings["remote_api_key"] else {}, timeout=8, trust_env=False)
        response.raise_for_status()
        return {"ok": True, "message": "远端模型服务连接成功。"}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(503, f"模型服务连接失败：{exc}") from exc


@app.post("/api/auth/logout")
def logout(request: Request, response: Response, user: Dict = Depends(current_user)):
    token = request.cookies.get(AUTH_COOKIE, "")
    with _auth_conn() as conn:
        conn.execute("DELETE FROM auth_sessions WHERE token_hash=?", (hashlib.sha256(token.encode()).hexdigest(),))
    response.delete_cookie(AUTH_COOKIE, path="/")
    return {"ok": True}


@app.get("/api/chat/sessions")
def list_chat_sessions(user: Dict = Depends(current_user)):
    with _auth_conn() as conn:
        rows = conn.execute("SELECT session_id,title,updated_at FROM chat_sessions WHERE user_id=? ORDER BY updated_at DESC", (user["id"],)).fetchall()
    return [dict(row) for row in rows]


@app.post("/api/chat/sessions")
def create_chat_session(payload: SessionCreateRequest, user: Dict = Depends(current_user)):
    if not payload.session_id or len(payload.session_id) > 100:
        raise HTTPException(400, "无效的会话编号")
    title = (payload.title or "新对话").strip()[:80] or "新对话"
    with _auth_conn() as conn:
        conn.execute("INSERT OR IGNORE INTO chat_sessions(session_id,user_id,title,updated_at) VALUES(?,?,?,?)", (payload.session_id,user["id"],title,datetime.now(timezone.utc).isoformat()))
        row = conn.execute("SELECT user_id FROM chat_sessions WHERE session_id=?", (payload.session_id,)).fetchone()
    if not row or row["user_id"] != user["id"]:
        raise HTTPException(404, "会话不存在")
    return {"session_id": payload.session_id, "title": title}


@app.delete("/api/chat/sessions/{session_id}")
def delete_chat_session(session_id: str, user: Dict = Depends(current_user)):
    require_owned_session(session_id, user)
    get_message_history(session_id).clear()
    with _auth_conn() as conn:
        conn.execute("DELETE FROM chat_sessions WHERE session_id=? AND user_id=?", (session_id,user["id"]))
    return {"ok": True}


def touch_chat_session(session_id: str, user: Dict, message: str):
    title = (message.strip().splitlines()[0][:48] or "新对话")
    with _auth_conn() as conn:
        conn.execute("INSERT OR IGNORE INTO chat_sessions(session_id,user_id,title,updated_at) VALUES(?,?,?,?)", (session_id,user["id"],title,datetime.now(timezone.utc).isoformat()))
        row = conn.execute("SELECT user_id,title FROM chat_sessions WHERE session_id=?", (session_id,)).fetchone()
        if not row or row["user_id"] != user["id"]:
            raise HTTPException(404, "会话不存在")
        if row["title"] == "新对话":
            conn.execute("UPDATE chat_sessions SET title=?,updated_at=? WHERE session_id=?", (title,datetime.now(timezone.utc).isoformat(),session_id))
        else:
            conn.execute("UPDATE chat_sessions SET updated_at=? WHERE session_id=?", (datetime.now(timezone.utc).isoformat(),session_id))

def build_chain():
    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", "你是结构生力理论研究助手，结合对话上下文与用户提供的理论资料回答问题，并跟随用户使用的语言作答。涉及理论依据时优先引用资料片段，用 [文件名] 标出来源；资料没有支撑时明确说明，区分资料内容与推断，不要编造理论定义。\n资料片段：\n{context}"),
            MessagesPlaceholder(variable_name="history"),
            ("human", "{input}"),
        ]
    )
    settings = model_settings.get()
    if settings["provider"] == "remote":
        model_name = settings["remote_chat_model"].strip()
        base_url = settings["remote_base_url"]
        api_key = settings["remote_api_key"] or "not-required"
        if not model_name:
            raise HTTPException(400, "请先在模型设置中填写远端聊天模型名称")
    else:
        model_name = settings["local_chat_model"]
        base_url = f"{settings['ollama_url']}/v1"
        api_key = "ollama"
    model = ChatOpenAI(
        model=model_name,
        api_key=api_key,
        base_url=base_url,
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


app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173"],
    allow_origin_regex=r"http://localhost:\d+",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/rag/files")
def rag_list_files(user: Dict = Depends(current_user)):
    return rags.list_files(user["id"])


@app.post("/api/rag/files", status_code=201)
async def rag_upload_file(file: UploadFile = File(...), user: Dict = Depends(current_user)):
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
    return rags.upsert_file(filename, content, user["id"])


@app.get("/api/rag/files/{file_id}")
def rag_preview_file(file_id: str, user: Dict = Depends(current_user)):
    document = rags.get_file(file_id, user["id"])
    if not document:
        raise HTTPException(status_code=404, detail="文件不存在")
    return document


@app.get("/api/rag/files/{file_id}/versions")
def rag_file_versions(file_id: str, user: Dict = Depends(current_user)):
    versions = rags.list_versions(file_id, user["id"])
    if versions is None:
        raise HTTPException(status_code=404, detail="文件不存在")
    return versions


@app.get("/api/rag/files/{file_id}/versions/{version_no}")
def rag_file_version(file_id: str, version_no: int, user: Dict = Depends(current_user)):
    version = rags.get_version(file_id, version_no, user["id"])
    if not version:
        raise HTTPException(status_code=404, detail="文件版本不存在")
    return version


@app.get("/api/rag/files/{file_id}/compare")
def rag_compare_file_versions(file_id: str, from_version: int, to_version: int, user: Dict = Depends(current_user)):
    result = rags.compare_versions(file_id, from_version, to_version, user["id"])
    if result is None:
        raise HTTPException(status_code=404, detail="文件或指定版本不存在")
    return result


@app.delete("/api/rag/files/{file_id}")
def rag_delete_file(file_id: str, user: Dict = Depends(current_user)):
    if not rags.delete_file(file_id, user["id"]):
        raise HTTPException(status_code=404, detail="文件不存在")
    return {"ok": True}


@app.post("/api/chat", response_model=ChatResponse)
def chat(req: ChatRequest, user: Dict = Depends(current_user)) -> ChatResponse:
    if not req.session_id or not req.message:
        raise HTTPException(status_code=400, detail="session_id 与 message 均为必填")
    touch_chat_session(req.session_id, user, req.message)
    chain = build_chain()
    docs = rags.search(req.message, k=4, owner_id=user["id"])
    context = "\n\n".join(
        f"[资料：{d['metadata']['filename']}]\n{d['content']}" for d in docs
    ) if docs else "（知识库暂无可检索资料）"
    try:
        reply = chain.invoke({"input": req.message, "context": context}, config={"configurable": {"session_id": req.session_id}})
    except Exception as exc:
        logger.exception("Chat completion failed")
        provider_label = "远端模型服务" if model_settings.get()["provider"] == "remote" else "本地 Ollama 服务"
        raise HTTPException(status_code=503, detail=f"{provider_label}调用失败，请检查全局模型设置与服务状态。") from exc
    return ChatResponse(session_id=req.session_id, reply=reply)

@app.get("/api/chat/stream")
def chat_stream(session_id: str, message: str, request: Request, user: Dict = Depends(current_user)):
    if not session_id or not message:
        raise HTTPException(status_code=400, detail="session_id 与 message 均为必填")
    touch_chat_session(session_id, user, message)
    chain = build_chain()
    docs = rags.search(message, k=4, owner_id=user["id"])
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
            detail = "模型服务回复失败，请检查全局模型设置与服务状态后重试。"
            yield f"event: error\ndata: {detail}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

class IngestRequest(BaseModel):
    content: str
    metadata: Optional[Dict] = None

@app.post("/api/rag/ingest")
def rag_ingest(req: IngestRequest, user: Dict = Depends(current_user)):
    if not req.content or len(req.content.strip()) == 0:
        raise HTTPException(status_code=400, detail="content 必填")
    rags.add(req.content.strip(), user["id"], req.metadata or {})
    return {"ok": True}

@app.get("/api/rag/search")
def rag_search(q: str, k: int = 5, user: Dict = Depends(current_user)):
    items = rags.search(q, k, owner_id=user["id"])
    return items

@app.get("/api/history/{session_id}")
def get_history(session_id: str, user: Dict = Depends(current_user)):
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id 必填")
    require_owned_session(session_id, user)
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
def clear_history(session_id: str, user: Dict = Depends(current_user)):
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id 必填")
    require_owned_session(session_id, user)
    history = get_message_history(session_id)
    try:
        history.clear()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"ok": True}


from .research import create_research_router

app.include_router(create_research_router(current_user, rags))
