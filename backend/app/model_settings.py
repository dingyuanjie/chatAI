"""持久化的全局模型设置：将提供方、服务地址、模型名称、向量模型及 API 密钥保存在本地 SQLite 中，供聊天和科研共享。"""
# 中文模块说明：全局模型设置模块，负责本地/远端模型参数校验、SQLite 持久化和密钥脱敏。

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlsplit


DEFAULT_DB = Path(__file__).resolve().parent.parent / "data" / "settings.sqlite"


def normalize_ollama_url(value: str) -> str:
    """去掉地址尾部斜线，并移除可选的 /v1 后缀，返回供 Ollama 原生 API 使用的根地址。"""
    url = value.strip().rstrip("/")
    if url.endswith("/v1"):
        url = url[:-3]
    return url


def validate_http_url(value: str, field: str) -> str:
    """校验地址使用 http/https 且包含网络位置，然后规范化尾部斜线。"""
    url = value.strip().rstrip("/")
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError(f"{field} 必须是有效的 http/https 地址")
    return url


class GlobalModelSettings:
    """全站共用的一份 SQLite 模型配置，而不是每个账号各自保存一套；公开响应隐藏真实密钥。"""

    def __init__(self, db_path: Path = DEFAULT_DB):
        """创建设置数据库目录和键值表；表为空时读取环境默认值，不强行写入默认配置。"""
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS model_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")

    @contextmanager
    def _conn(self):
        """提供提交/回滚/关闭行为一致的 SQLite 上下文，避免每个设置操作重复管理连接。"""
        conn = sqlite3.connect(self.db_path.as_posix(), timeout=10)
        conn.execute("PRAGMA busy_timeout=10000")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _env_defaults() -> Dict[str, str]:
        """读取环境变量作为数据库尚未覆盖时的默认配置，包含本地、远端和向量模型字段。"""
        ollama_api = os.getenv("OLLAMA_BASE_URL") or "http://127.0.0.1:11434/v1"
        ollama_url = os.getenv("OLLAMA_API_BASE") or ollama_api.removesuffix("/v1")
        return {
            "provider": os.getenv("MODEL_PROVIDER", "local"),
            "ollama_url": normalize_ollama_url(ollama_url),
            "remote_base_url": os.getenv("OPENAI_API_BASE", "https://api.openai.com/v1").rstrip("/"),
            "remote_api_key": os.getenv("OPENAI_API_KEY", ""),
            "local_chat_model": os.getenv("LOCAL_MODEL", "chatai-local"),
            "local_research_model": os.getenv("RESEARCH_MODEL", "qwen3:4b-instruct-2507-q4_K_M"),
            "remote_chat_model": os.getenv("REMOTE_CHAT_MODEL", ""),
            "remote_research_model": os.getenv("REMOTE_RESEARCH_MODEL", ""),
            "embedding_model": os.getenv("EMBEDDING_MODEL", "qwen3-embedding:0.6b"),
        }

    def get(self) -> Dict[str, str]:
        """先装载环境默认值，再用 SQLite 已保存键值覆盖，返回完整有效设置。"""
        settings = self._env_defaults()
        with self._conn() as conn:
            settings.update(dict(conn.execute("SELECT key,value FROM model_settings").fetchall()))
        return settings

    @classmethod
    def validate_update(cls, values: Dict[str, Any]) -> Dict[str, str]:
        """仅接受白名单字段，校验 provider、模型名称长度和服务地址，并处理显式清除密钥请求。"""
        allowed = set(cls._env_defaults())
        updates = {key: str(value) for key, value in values.items() if key in allowed and value is not None}
        if "provider" in updates and updates["provider"] not in ("local", "remote"):
            raise ValueError("provider 只能是 local 或 remote")
        for key in ("local_chat_model", "local_research_model", "remote_chat_model", "remote_research_model", "embedding_model"):
            if key in updates and len(updates[key].strip()) > 180:
                raise ValueError(f"{key} 长度不能超过 180 个字符")
            if key in updates:
                updates[key] = updates[key].strip()
        if "ollama_url" in updates:
            updates["ollama_url"] = normalize_ollama_url(validate_http_url(updates["ollama_url"], "Ollama 地址"))
        if "remote_base_url" in updates:
            updates["remote_base_url"] = validate_http_url(updates["remote_base_url"], "远端 API 地址")
        if values.get("clear_api_key"):
            updates["remote_api_key"] = ""
        return updates

    def update(self, values: Dict[str, Any]) -> Dict[str, str]:
        """校验后以键值 UPSERT 持久化配置，并重新读取完整配置作为返回结果。"""
        updates = self.validate_update(values)
        with self._conn() as conn:
            conn.executemany("INSERT INTO model_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", updates.items())
        return self.get()

    def public(self) -> Dict[str, Any]:
        """生成浏览器可见设置：移除 API 密钥原值，仅添加 api_key_configured 布尔标记。"""
        settings = self.get()
        settings["api_key_configured"] = bool(settings.pop("remote_api_key", ""))
        return settings


model_settings = GlobalModelSettings()
