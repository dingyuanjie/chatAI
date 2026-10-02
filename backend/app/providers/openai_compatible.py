"""Chat-completions adapter for Ollama and other OpenAI-compatible servers."""

import os

import httpx

from .models import ModelResponse


class OpenAICompatibleChatModel:
    def __init__(self, base_url: str, model: str, api_key: str = "ollama", timeout: float = 600):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout

    @classmethod
    def from_environment(cls) -> "OpenAICompatibleChatModel":
        return cls(
            os.getenv("RESEARCH_BASE_URL") or os.getenv("OLLAMA_BASE_URL") or "http://127.0.0.1:11434/v1",
            os.getenv("RESEARCH_MODEL") or os.getenv("LOCAL_MODEL") or "chatai-local",
            os.getenv("RESEARCH_API_KEY") or "ollama",
            float(os.getenv("RESEARCH_TIMEOUT_SECONDS") or "600"),
        )

    def generate(self, system: str, prompt: str, *, temperature: float = 0.35, max_tokens: int = 2048) -> ModelResponse:
        response = httpx.post(
            f"{self.base_url}/chat/completions",
            json={"model": self.model, "stream": False, "temperature": temperature, "max_tokens": max_tokens,
                  "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]},
            headers={"Authorization": f"Bearer {self.api_key}"}, timeout=self.timeout, trust_env=False,
        )
        response.raise_for_status()
        choice = response.json().get("choices", [{}])[0]
        text = choice.get("message", {}).get("content", "").strip()
        finish_reason = choice.get("finish_reason") or ""
        if not text:
            if finish_reason == "length":
                raise RuntimeError("模型思考过程耗尽了生成长度，请检查研究输出长度设置")
            raise RuntimeError("本地模型没有返回正文")
        return ModelResponse(text=text, model=self.model, finish_reason=finish_reason)

