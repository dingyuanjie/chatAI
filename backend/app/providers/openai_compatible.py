"""Chat-completions adapter for Ollama and other OpenAI-compatible servers."""

import os

import httpx

from app.model_settings import model_settings
from .models import ModelResponse


class OpenAICompatibleChatModel:
    def __init__(self, base_url: str, model: str, api_key: str = "ollama", timeout: float = 600):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout

    @classmethod
    def from_environment(cls) -> "OpenAICompatibleChatModel":
        settings = model_settings.get()
        if settings["provider"] == "remote":
            base_url = settings["remote_base_url"]
            model = settings["remote_research_model"] or settings["remote_chat_model"]
            api_key = settings["remote_api_key"]
            if not model:
                raise RuntimeError("请先在全局模型设置中填写远端科研模型或聊天模型名称")
        else:
            base_url = f"{settings['ollama_url']}/v1"
            model = settings["local_research_model"]
            api_key = "ollama"
        return cls(
            base_url,
            model,
            api_key or "ollama",
            float(os.getenv("RESEARCH_TIMEOUT_SECONDS") or "600"),
        )

    def generate(self, system: str, prompt: str, *, temperature: float = 0.35, max_tokens: int = 2048) -> ModelResponse:
        response = httpx.post(
            f"{self.base_url}/chat/completions",
            json={"model": self.model, "stream": False, "temperature": temperature, "max_tokens": max_tokens,
                  "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]},
            headers={"Authorization": f"Bearer {self.api_key}"}, timeout=self.timeout, trust_env=False,
        )
        if response.status_code >= 400:
            detail = response.text[:1200]
            try:
                error_payload = response.json().get("error", {})
                detail = error_payload.get("message", detail) if isinstance(error_payload, dict) else str(error_payload or detail)
            except (ValueError, AttributeError):
                pass
            normalized = detail.casefold()
            if response.status_code == 400 and any(marker in normalized for marker in (
                "context length", "context window", "input length", "num_ctx", "prompt is too long", "exceeds the available context"
            )):
                raise RuntimeError("本地模型上下文窗口不足，提示词与生成长度超过模型限制。系统已记录服务端说明；请缩短研究输入或调整模型上下文长度。")
            raise RuntimeError(f"模型服务请求失败（HTTP {response.status_code}）：{detail}")
        choice = response.json().get("choices", [{}])[0]
        text = choice.get("message", {}).get("content", "").strip()
        finish_reason = choice.get("finish_reason") or ""
        if not text:
            if finish_reason == "length":
                raise RuntimeError("模型思考过程耗尽了生成长度，请检查研究输出长度设置")
            raise RuntimeError("模型服务没有返回正文")
        return ModelResponse(text=text, model=self.model, finish_reason=finish_reason)
