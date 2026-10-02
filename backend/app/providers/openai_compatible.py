"""Chat-completions adapter for Ollama and other OpenAI-compatible servers."""

import os

import httpx

from app.model_settings import model_settings
from .models import ModelResponse


def completion_token_limit(provider: str, model: str, requested: int) -> int:
    """Keep profile-sized local outputs, but allow enough room for hosted reasoning models."""
    if provider != "remote" or "deepseek" not in model.casefold():
        return requested
    model_id = model.casefold()
    # DeepSeek reasoning tokens and the visible answer share max_tokens. The
    # research profiles' 800–1,600 token caps can be consumed before an answer
    # is produced, even when the prompt easily fits the context window.
    minimum = 16_384 if any(marker in model_id for marker in ("reasoner", "r1", "thinking", "v4", "flash")) else 8_192
    return max(requested, minimum)


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
        settings = model_settings.get()
        output_limit = completion_token_limit(settings["provider"], self.model, max_tokens)
        response = httpx.post(
            f"{self.base_url}/chat/completions",
            json={"model": self.model, "stream": False, "temperature": temperature, "max_tokens": output_limit,
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
                raise RuntimeError("模型服务报告输入或生成长度超过上下文窗口限制；这与单次输出上限不同。请查看服务端返回的具体限制。")
            raise RuntimeError(f"模型服务请求失败（HTTP {response.status_code}）：{detail}")
        choice = response.json().get("choices", [{}])[0]
        text = choice.get("message", {}).get("content", "").strip()
        finish_reason = choice.get("finish_reason") or ""
        if not text:
            if finish_reason == "length":
                raise RuntimeError(f"模型达到本次生成上限（max_tokens={output_limit}），没有留下可用正文；这不一定表示输入超出上下文窗口。")
            raise RuntimeError("模型服务没有返回正文")
        return ModelResponse(text=text, model=self.model, finish_reason=finish_reason)
