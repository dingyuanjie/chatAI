# 中文模块说明：FastAPI 应用入口，汇总身份认证、模型设置、对话会话、Markdown 知识库、RAG 检索和流式聊天 API，并初始化共享存储组件。
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
from app.providers.openai_compatible import completion_token_limit

logger = logging.getLogger(__name__)

class ChatRequest(BaseModel):
    """对话请求模型。session_id 用于关联历史会话，message 是本轮用户输入；FastAPI/Pydantic 在进入处理逻辑前完成字段类型校验。"""
    session_id: str
    message: str


class ChatResponse(BaseModel):
    """对话响应模型。返回会话标识和模型正文，供普通 HTTP 对话接口保持稳定的 JSON 结构。"""
    session_id: str
    reply: str


# 所有运行时 SQLite 文件集中在本地 data 目录，包含对话历史、账号和知识库数据；该目录不进入源码版本控制。
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
    """按会话查询消息并按创建时间排序，将数据库记录转换为前端聊天历史结构。"""
    return SQLChatMessageHistory(connection_string=SQLITE_URL, session_id=session_id)

class RAGStore:
    """SQLite 知识库仓储。它同时维护 Markdown 原文、版本历史、分块文本、向量模型标记和全文索引；每个文件及召回结果都按 owner_id 隔离。"""
    def __init__(self, db_path: Path):
        """保存知识库数据库路径并立即执行幂等建表/迁移，保证服务第一次启动和后续启动都能使用同一套表结构。"""
        self.db_path = db_path
        self._init_db()

    def _conn(self):
        """创建当前 RAG 数据库连接。调用方负责使用完毕后关闭连接；连接只面向本地 SQLite 文件。"""
        return sqlite3.connect(self.db_path.as_posix())

    def _init_db(self):
        """创建全文索引、知识文件、分块、嵌入和版本表，并为旧库补充缺失字段/初始版本。所有迁移可重复执行，不删除既有用户内容。"""
        conn = self._conn()
        try:
            cur = conn.cursor()
            # docs 是 SQLite FTS5 全文索引；knowledge_files/chunks 则保存原文和向量，是知识库的结构化存储。
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
        """将 Markdown 原文归一化换行后切成有重叠的文本块；优先在换行处断开，重叠区域保留上下文，减少答案信息跨块丢失。"""
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
        """调用配置中的本地 Ollama 向量模型，把一批文本转换为向量。校验返回向量数量和非空性；服务异常转换为 503，避免写入不完整索引。"""
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
        """按批次重新计算所有知识分块的向量，并记录新的 embedding_model 标记。分批处理可控制请求体和内存用量，返回完成更新的块数。"""
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
        """只列出当前用户的知识文件，并汇总每个文件的分块数和最新版本号，用于知识库管理界面。"""
        with self._conn() as conn:
            rows = conn.execute("""SELECT f.id, f.filename, f.created_at, COUNT(DISTINCT c.id), COALESCE(MAX(v.version_no),1)
                FROM knowledge_files f LEFT JOIN knowledge_chunks c ON c.file_id=f.id
                LEFT JOIN knowledge_file_versions v ON v.file_id=f.id
                WHERE f.owner_id=? GROUP BY f.id ORDER BY f.created_at DESC""", (owner_id,)).fetchall()
        return [{"id": row[0], "filename": row[1], "created_at": row[2], "chunks": row[3], "version": row[4]} for row in rows]

    def claim_legacy_files(self, owner_id: str):
        """把早期版本遗留的 legacy 文件归属给首次登录用户，提供平滑升级兼容；后续读取仍按用户 ID 限制。"""
        with self._conn() as conn:
            conn.execute("UPDATE knowledge_files SET owner_id=? WHERE owner_id='legacy'", (owner_id,))

    def get_file(self, file_id: str, owner_id: str) -> Optional[Dict]:
        """按文件 ID 和用户 ID 读取 Markdown 原文及当前版本；用户不匹配时视为不存在，避免泄露其他账号的文件。"""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT id, filename, content, created_at FROM knowledge_files WHERE id=? AND owner_id=?", (file_id, owner_id)
            ).fetchone()
            version = conn.execute("SELECT COALESCE(MAX(version_no),1) FROM knowledge_file_versions WHERE file_id=?", (file_id,)).fetchone()[0] if row else None
        return {"id": row[0], "filename": row[1], "content": row[2], "created_at": row[3], "version": version} if row else None

    def upsert_file(self, filename: str, content: str, owner_id: str) -> Dict:
        """新增或更新用户的 Markdown 文件。先用 SHA-256 识别相同内容并跳过重复向量化；内容变化时保存新版本、分块和向量，并同步全文索引。"""
        # 先比较内容哈希：相同文件重复上传时不新增版本，也不重复调用向量模型。
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        with self._conn() as conn:
            old = conn.execute("SELECT id, content FROM knowledge_files WHERE filename=? COLLATE NOCASE AND owner_id=?", (filename, owner_id)).fetchone()
            if old and hashlib.sha256(old[1].encode("utf-8")).hexdigest() == content_hash:
                version = conn.execute("SELECT COALESCE(MAX(version_no),1) FROM knowledge_file_versions WHERE file_id=?", (old[0],)).fetchone()[0]
                count = conn.execute("SELECT COUNT(*) FROM knowledge_chunks WHERE file_id=?", (old[0],)).fetchone()[0]
                return {"id": old[0], "filename": filename, "chunks": count, "version": version, "unchanged": True}
        # 新向量必须记录生成模型名称；切换全局嵌入模型后，search 会据此拒绝混用不同向量空间。
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
        """确认文件归属后按版本号倒序返回版本摘要，不返回整篇正文；文件不属于当前用户时返回 None。"""
        with self._conn() as conn:
            if not conn.execute("SELECT 1 FROM knowledge_files WHERE id=? AND owner_id=?", (file_id, owner_id)).fetchone():
                return None
            rows = conn.execute("SELECT version_no,content_hash,length(content),created_at FROM knowledge_file_versions WHERE file_id=? ORDER BY version_no DESC", (file_id,)).fetchall()
        return [{"version":r[0],"content_hash":r[1],"characters":r[2],"created_at":r[3]} for r in rows]

    def get_version(self, file_id: str, version_no: int, owner_id: str) -> Optional[Dict]:
        """按文件、版本号和用户身份读取某一历史版本原文；所有条件在同一查询中约束，防止跨用户访问。"""
        with self._conn() as conn:
            row = conn.execute("SELECT v.version_no,v.content,v.created_at,f.filename FROM knowledge_file_versions v JOIN knowledge_files f ON f.id=v.file_id WHERE v.file_id=? AND v.version_no=? AND f.owner_id=?", (file_id,version_no,owner_id)).fetchone()
        return {"version":row[0],"content":row[1],"created_at":row[2],"filename":row[3]} if row else None

    def compare_versions(self, file_id: str, from_version: int, to_version: int, owner_id: str) -> Optional[Dict]:
        """读取指定的两个版本并生成统一 diff；任一版本不可见或不存在时返回 None，由 API 层映射为 404。"""
        before = self.get_version(file_id, from_version, owner_id)
        after = self.get_version(file_id, to_version, owner_id)
        if not before or not after:
            return None
        diff = "".join(difflib.unified_diff(before["content"].splitlines(True), after["content"].splitlines(True), fromfile=f"v{from_version}", tofile=f"v{to_version}"))
        return {"from_version":from_version,"to_version":to_version,"diff":diff}

    def delete_file(self, file_id: str, owner_id: str) -> bool:
        """删除指定用户的知识文件和关联全文索引；启用外键级联以清除分块和版本记录，并通过受影响行数报告是否存在。"""
        with self._conn() as conn:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("DELETE FROM docs WHERE json_extract(metadata, '$.file_id')=?", (file_id,))
            cursor = conn.execute("DELETE FROM knowledge_files WHERE id=? AND owner_id=?", (file_id, owner_id))
        return cursor.rowcount > 0

    def add(self, content: str, owner_id: str, metadata: Optional[Dict] = None):
        """兼容手动录入知识的旧调用接口，把内容委托给统一 upsert 流程，使手动笔记也会生成分块、向量和版本记录。"""
        return self.upsert_file((metadata or {}).get("filename", "手动录入.md"), content, owner_id)

    @staticmethod
    def _cosine(left: List[float], right: List[float]) -> float:
        """计算两个向量的余弦相似度；零向量分母返回 0，避免除零。向量长度不一致时只对成对分量计算，调用方应使用同一嵌入模型。"""
        denominator = math.sqrt(sum(x * x for x in left) * sum(y * y for y in right))
        return sum(x * y for x, y in zip(left, right)) / denominator if denominator else 0.0

    def search(self, query: str, k: int = 5, owner_id: str = "legacy") -> List[Dict]:
        """先检查查询、用户文件和向量模型版本，再为查询生成向量并按用户范围计算相似度，返回最相关文本块及文件元数据；旧模型索引会返回 409 提示重建。"""
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
        # 查询和文件分块共用当前本地嵌入模型；后续只对当前用户持有的分块进行相似度比较。
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
    """创建身份认证数据库连接，并设置忙等待时间；上下文正常退出时提交，异常时回滚，最终总会关闭连接。"""
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
    """使用随机盐和 PBKDF2-HMAC-SHA256 派生密码摘要。数据库不保存明文密码；摘要格式同时保存盐和哈希，便于登录验证。"""
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 310_000)
    return f"{salt.hex()}${digest.hex()}"


def _password_matches(password: str, stored: str) -> bool:
    """解析已保存的盐值并重新计算密码摘要，使用恒定时间比较函数检查一致性；格式错误或类型错误统一判定为不匹配。"""
    try:
        salt_hex, digest_hex = stored.split("$", 1)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), 310_000).hex()
        return hmac.compare_digest(actual, digest_hex)
    except (ValueError, TypeError):
        return False


def _public_user(user: sqlite3.Row) -> Dict:
    """把数据库用户行转换为前端需要的公开字段，不返回密码摘要、会话令牌或其他认证内部信息。"""
    return {"id": user["id"], "email": user["email"], "name": user["display_name"]}


def current_user(request: Request) -> Dict:
    """从 HttpOnly Cookie 读取随机会话令牌，先计算令牌哈希，再检查数据库中的有效期并加载用户；Cookie 缺失或过期时返回 401。"""
    token = request.cookies.get(AUTH_COOKIE)
    if not token:
        raise HTTPException(401, "请先登录")
    # 数据库只保存令牌哈希，避免认证表泄露后令牌可被直接重放。
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    with _auth_conn() as conn:
        row = conn.execute("SELECT u.id, u.email, u.display_name FROM auth_sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires_at>?", (token_hash, int(time.time()))).fetchone()
    if not row:
        raise HTTPException(401, "登录已过期，请重新登录")
    return _public_user(row)


def require_owned_session(session_id: str, user: Dict):
    """检查会话编号是否属于当前已认证用户。后续读取、清除历史前都必须调用此校验，避免猜测其他人的会话 ID。"""
    with _auth_conn() as conn:
        row = conn.execute("SELECT 1 FROM chat_sessions WHERE session_id=? AND user_id=?", (session_id, user["id"])).fetchone()
    if not row:
        raise HTTPException(404, "会话不存在")


def _issue_session(user_id: str, response: Response):
    """生成高熵随机会话令牌，只将 SHA-256 哈希和过期时间写入数据库，并把原令牌放入 HttpOnly、SameSite=Lax Cookie。"""
    # 原始随机令牌只通过 HttpOnly Cookie 返回浏览器；服务端只保存哈希和过期时间。
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    with _auth_conn() as conn:
        conn.execute("INSERT INTO auth_sessions(token_hash,user_id,expires_at) VALUES(?,?,?)", (token_hash, user_id, int(time.time()) + AUTH_MAX_AGE))
    response.set_cookie(AUTH_COOKIE, token, max_age=AUTH_MAX_AGE, httponly=True, samesite="lax", secure=False, path="/")


class AuthRequest(BaseModel):
    """登录/注册请求模型，统一接收邮箱、密码和可选显示名称；账号规则由对应路由进一步验证。"""
    email: str
    password: str
    name: Optional[str] = None


class SessionCreateRequest(BaseModel):
    """新建聊天会话请求模型，接受前端生成的会话 ID 和可选标题。"""
    session_id: str
    title: Optional[str] = None


@app.post("/api/auth/register")
def register(payload: AuthRequest, response: Response):
    """规范化邮箱并验证密码长度，创建用户后迁移其名下旧版知识库文件，最后签发登录 Cookie；邮箱唯一约束冲突映射为 409。"""
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
    """根据规范化邮箱查找账号并校验 PBKDF2 密码摘要；仅验证通过后签发新会话 Cookie，失败统一返回认证错误。"""
    email = payload.email.strip().lower()
    with _auth_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE email=? COLLATE NOCASE", (email,)).fetchone()
    if not row or not _password_matches(payload.password, row["password_hash"]):
        raise HTTPException(401, "邮箱或密码不正确")
    _issue_session(row["id"], response)
    return _public_user(row)


@app.get("/api/auth/me")
def auth_me(user: Dict = Depends(current_user)):
    """返回当前认证依赖解析出的公开用户信息，用于前端刷新时恢复登录态。"""
    return user


@app.get("/api/settings/models")
def get_model_settings(user: Dict = Depends(current_user)):
    """读取全局模型设置的公开视图；API 密钥只返回“是否已配置”，不会把真实密钥发送到浏览器。"""
    return model_settings.public()


@app.get("/api/settings/models/available")
def available_models(user: Dict = Depends(current_user)):
    """分别查询本地 Ollama 和当前远端兼容 API 的模型列表；单个服务不可用时保留错误描述，并仍返回另一端可用列表。"""
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
    """校验并更新全局模型配置。向量模型或 Ollama 地址发生变化时，先重建旧知识库向量索引，成功后再持久化新配置，避免设置与向量版本不一致。"""
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
    """验证模型设置是否能连通：检查 Ollama、向量模型、所选本地模型，或检查远端 API 与模型列表；只测试传入的临时配置，不保存设置。"""
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
    """删除当前 Cookie 对应的数据库会话记录并清除浏览器 Cookie；用户密码和聊天内容不会因此删除。"""
    token = request.cookies.get(AUTH_COOKIE, "")
    with _auth_conn() as conn:
        conn.execute("DELETE FROM auth_sessions WHERE token_hash=?", (hashlib.sha256(token.encode()).hexdigest(),))
    response.delete_cookie(AUTH_COOKIE, path="/")
    return {"ok": True}


@app.get("/api/chat/sessions")
def list_chat_sessions(user: Dict = Depends(current_user)):
    """仅返回当前用户的会话列表，按最近活动时间倒序排列。"""
    with _auth_conn() as conn:
        rows = conn.execute("SELECT session_id,title,updated_at FROM chat_sessions WHERE user_id=? ORDER BY updated_at DESC", (user["id"],)).fetchall()
    return [dict(row) for row in rows]


@app.post("/api/chat/sessions")
def create_chat_session(payload: SessionCreateRequest, user: Dict = Depends(current_user)):
    """校验前端提供的会话 ID 和标题后执行幂等创建；如果该 ID 已被其他用户占用，则不返回该会话。"""
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
    """确认会话归属后清除 LangChain 消息历史，再删除用户会话目录记录。"""
    require_owned_session(session_id, user)
    get_message_history(session_id).clear()
    with _auth_conn() as conn:
        conn.execute("DELETE FROM chat_sessions WHERE session_id=? AND user_id=?", (session_id,user["id"]))
    return {"ok": True}


def touch_chat_session(session_id: str, user: Dict, message: str):
    """在每次对话时创建或刷新会话活动时间；首条消息用作简短标题，并在事务内再次核对所属用户。"""
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
    """按当前全局模型设置构造聊天链。系统提示要求回答优先依据个人知识库并区分事实与推断；链路由提示词、ChatOpenAI 兼容客户端、字符串解析器和 SQLite 消息历史组成。"""
    # 检索上下文与会话历史作为不同变量注入，提示词要求优先使用用户知识库并注明推断边界。
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
    chat_output_tokens = completion_token_limit(settings["provider"], model_name, 4096)
    model = ChatOpenAI(
        model=model_name,
        api_key=api_key,
        base_url=base_url,
        temperature=0.3,
        max_tokens=chat_output_tokens,
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
    """返回当前用户的 Markdown 知识文件清单，不暴露其他用户文件。"""
    return rags.list_files(user["id"])


@app.post("/api/rag/files", status_code=201)
async def rag_upload_file(file: UploadFile = File(...), user: Dict = Depends(current_user)):
    """接收 Markdown 文件，清理路径部分并限制扩展名、大小和 UTF-8 编码；验证非空后交给仓储执行版本化、分块与向量化。"""
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
    """按当前用户权限读取文件原文供 Markdown 预览；文件不存在或不属于当前用户时返回 404。"""
    document = rags.get_file(file_id, user["id"])
    if not document:
        raise HTTPException(status_code=404, detail="文件不存在")
    return document


@app.get("/api/rag/files/{file_id}/versions")
def rag_file_versions(file_id: str, user: Dict = Depends(current_user)):
    """列出当前用户文件的历史版本摘要，资源不可见时返回 404。"""
    versions = rags.list_versions(file_id, user["id"])
    if versions is None:
        raise HTTPException(status_code=404, detail="文件不存在")
    return versions


@app.get("/api/rag/files/{file_id}/versions/{version_no}")
def rag_file_version(file_id: str, version_no: int, user: Dict = Depends(current_user)):
    """读取某个历史版本的 Markdown 正文，供单独预览历史内容。"""
    version = rags.get_version(file_id, version_no, user["id"])
    if not version:
        raise HTTPException(status_code=404, detail="文件版本不存在")
    return version


@app.get("/api/rag/files/{file_id}/compare")
def rag_compare_file_versions(file_id: str, from_version: int, to_version: int, user: Dict = Depends(current_user)):
    """比较同一文件的两个版本并返回差异文本；文件或版本不存在时返回 404。"""
    result = rags.compare_versions(file_id, from_version, to_version, user["id"])
    if result is None:
        raise HTTPException(status_code=404, detail="文件或指定版本不存在")
    return result


@app.delete("/api/rag/files/{file_id}")
def rag_delete_file(file_id: str, user: Dict = Depends(current_user)):
    """删除当前用户的知识库文件及其索引；未找到或无权访问时返回 404。"""
    if not rags.delete_file(file_id, user["id"]):
        raise HTTPException(status_code=404, detail="文件不存在")
    return {"ok": True}


@app.post("/api/chat", response_model=ChatResponse)
def chat(req: ChatRequest, user: Dict = Depends(current_user)) -> ChatResponse:
    """处理一次非流式对话：校验输入和会话权限、检索最多四个相关资料块、构造带历史的模型链并返回完整回复；模型错误映射为 503。"""
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
    """建立服务器发送事件流。每个生成片段编码为 SSE data 行，完成后发送 done；空回复和模型错误发送 error 事件，前端据此结束加载态。"""
    if not session_id or not message:
        raise HTTPException(status_code=400, detail="session_id 与 message 均为必填")
    touch_chat_session(session_id, user, message)
    chain = build_chain()
    docs = rags.search(message, k=4, owner_id=user["id"])
    context = "\n\n".join(
        f"[资料：{d['metadata']['filename']}]\n{d['content']}" for d in docs
    ) if docs else "（知识库暂无可检索资料）"

    def event_generator():
        """逐段读取模型流式响应，解析 SSE 数据帧并产出前端可消费的文本增量与结束事件。"""
        try:
            yield ": connected\n\n"
            has_content = False
            for chunk in chain.stream({"input": message, "context": context}, config={"configurable": {"session_id": session_id}}):
                if not chunk:
                    continue
                has_content = True
                # SSE 规范要求每一行都带 data: 前缀；空行也要编码，浏览器才能还原模型正文换行。
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
    """手动导入知识请求模型；content 是待检索正文，metadata 可携带来源文件名等附加信息。"""
    content: str
    metadata: Optional[Dict] = None

@app.post("/api/rag/ingest")
def rag_ingest(req: IngestRequest, user: Dict = Depends(current_user)):
    """验证手动录入正文非空，将其作为一个知识文件写入与上传文件相同的版本化/向量化流程。"""
    if not req.content or len(req.content.strip()) == 0:
        raise HTTPException(status_code=400, detail="content 必填")
    rags.add(req.content.strip(), user["id"], req.metadata or {})
    return {"ok": True}

@app.get("/api/rag/search")
def rag_search(q: str, k: int = 5, user: Dict = Depends(current_user)):
    """以当前用户身份执行调试/预览用的知识库相似度搜索，并返回匹配块与来源信息。"""
    items = rags.search(q, k, owner_id=user["id"])
    return items

@app.get("/api/history/{session_id}")
def get_history(session_id: str, user: Dict = Depends(current_user)):
    """确认会话归属后读取消息历史，转换成前端统一的 role/content 数组；底层历史读取失败时返回明确的 500。"""
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
    """确认会话归属后删除该会话消息记录；只清聊天历史，不删除会话和知识文件。"""
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
