"""Chat-completions adapter for Ollama and other OpenAI-compatible servers."""

import os
from typing import Iterator, Optional
from urllib.parse import urlparse

import httpx

from app.model_settings import model_settings
from .models import ModelCapabilities, ModelProviderError, ModelResponse, ModelUsage
from .pricing import ProviderPricingConfig


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
    def __init__(self, base_url: str, model: str, api_key: str = "ollama", timeout: float = 600,
                 provider_type: str = "REMOTE", provider_id: str = "openai_compatible"):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self.provider_type = "LOCAL" if str(provider_type).upper() == "LOCAL" else "REMOTE"
        self.provider_id = provider_id
        self.capabilities = ModelCapabilities(provider_type=self.provider_type, supports_usage_reporting=True,
            supports_cost_reporting=bool(ProviderPricingConfig.load().get(model)))
        self._last_usage: Optional[ModelUsage] = None

    @classmethod
    def from_environment(cls) -> "OpenAICompatibleChatModel":
        return cls.from_settings(model_settings.get())

    @classmethod
    def from_settings(cls, settings: dict) -> "OpenAICompatibleChatModel":
        if settings["provider"] == "remote":
            base_url = settings["remote_base_url"]
            model = settings["remote_research_model"] or settings["remote_chat_model"]
            api_key = settings["remote_api_key"]
            provider_type, provider_id = "REMOTE", "openai_compatible"
            hostname = urlparse(base_url).hostname
            if hostname:
                provider_id = hostname.lower()
            if not model:
                raise ModelProviderError("请先在全局模型设置中填写远端科研模型或聊天模型名称", "MODEL_UNAVAILABLE")
        else:
            base_url = f"{settings['ollama_url']}/v1"
            model = settings["local_research_model"]
            api_key = "ollama"
            provider_type, provider_id = "LOCAL", "ollama"
            if not model:
                raise ModelProviderError("请先在全局模型设置中填写本地科研模型名称", "MODEL_UNAVAILABLE")
        return cls(
            base_url,
            model,
            api_key or "ollama",
            float(os.getenv("RESEARCH_TIMEOUT_SECONDS") or "600"),
            provider_type=provider_type, provider_id=provider_id,
        )

    def generate(self, system: str, prompt: str, *, temperature: float = 0.35, max_tokens: int = 2048) -> ModelResponse:
        output_limit = completion_token_limit("remote" if self.provider_type == "REMOTE" else "local", self.model, max_tokens)
        try:
            response = httpx.post(
                f"{self.base_url}/chat/completions",
                json={"model": self.model, "stream": False, "temperature": temperature, "max_tokens": output_limit,
                      "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]},
                headers={"Authorization": f"Bearer {self.api_key}"}, timeout=self.timeout, trust_env=False,
            )
        except httpx.TimeoutException as exc:
            code = "LOCAL_RESOURCE_ERROR" if self.provider_type == "LOCAL" else "REMOTE_NETWORK_ERROR"
            raise ModelProviderError(f"模型调用超时：{exc}", code) from exc
        except httpx.RequestError as exc:
            code = "MODEL_UNAVAILABLE" if self.provider_type == "LOCAL" else "REMOTE_NETWORK_ERROR"
            raise ModelProviderError(f"模型服务连接失败：{exc}", code) from exc
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
            code = "MODEL_UNAVAILABLE" if self.provider_type == "LOCAL" else "REMOTE_SERVER_ERROR"
            if self.provider_type == "REMOTE" and response.status_code in (401, 403):
                code = "REMOTE_AUTH_ERROR"
            elif self.provider_type == "REMOTE" and any(term in normalized for term in ("quota", "balance", "billing", "insufficient_quota")):
                code = "API_QUOTA_EXHAUSTED"
            elif self.provider_type == "REMOTE" and response.status_code == 429:
                code = "REMOTE_RATE_LIMIT"
            elif self.provider_type == "LOCAL" and any(term in normalized for term in ("out of memory", "cuda out of memory", "memory allocation")):
                code = "LOCAL_RESOURCE_ERROR"
            raise ModelProviderError(f"模型服务请求失败（HTTP {response.status_code}）：{detail}", code, status_code=response.status_code)
        choice = response.json().get("choices", [{}])[0]
        text = choice.get("message", {}).get("content", "").strip()
        finish_reason = choice.get("finish_reason") or ""
        if not text:
            if finish_reason == "length":
                raise RuntimeError(f"模型达到本次生成上限（max_tokens={output_limit}），没有留下可用正文；这不一定表示输入超出上下文窗口。")
            raise RuntimeError("模型服务没有返回正文")
        usage = self.estimate_cost(ModelUsage.from_payload(response.json().get("usage")))
        self._last_usage = usage
        return ModelResponse(text=text, model=self.model, finish_reason=finish_reason, provider_id=self.provider_id,
                             provider_type=self.provider_type, usage=usage, capabilities=self.capabilities)

    def stream(self, system: str, prompt: str, *, temperature: float = 0.35, max_tokens: int = 2048) -> Iterator[str]:
        raise NotImplementedError("Streaming is not enabled for this adapter")

    def get_usage(self) -> Optional[ModelUsage]:
        return self._last_usage

    def estimate_cost(self, usage: Optional[ModelUsage]) -> Optional[ModelUsage]:
        if self.provider_type != "REMOTE":
            return usage
        return ProviderPricingConfig.estimate(self.model, usage)

    def health_check(self) -> bool:
        try:
            response = httpx.get(f"{self.base_url}/models", headers={"Authorization": f"Bearer {self.api_key}"},
                                 timeout=min(self.timeout, 8), trust_env=False)
            return response.status_code < 400
        except httpx.RequestError:
            return False
