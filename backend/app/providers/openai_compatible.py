"""OpenAI-compatible Chat Completions 适配器，支持本地 Ollama 与远端兼容 API。负责认证头、超时、错误分类、用量解析和价格估算。"""
# 中文模块说明：模型提供方抽象与适配层，统一模型请求、能力描述、用量解析、费用估算和后端工厂选择，避免业务逻辑绑定单一供应商。

import os
from typing import Iterator, Optional
from urllib.parse import urlparse

import httpx

from app.model_settings import model_settings
from .models import ModelCapabilities, ModelProviderError, ModelResponse, ModelUsage
from .pricing import ProviderPricingConfig


def completion_token_limit(provider: str, model: str, requested: int) -> int:
    """本地生成长度遵循专家档案限制；DeepSeek 远端推理模型会额外预留推理 Token 空间，避免生成预算耗尽后只返回空正文。"""
    if provider != "remote" or "deepseek" not in model.casefold():
        return requested
    model_id = model.casefold()
    # DeepSeek 将推理 Token 与可见答案共用 max_tokens；专家档案原本较小的输出预算
    # 可能在正文开始前就被推理过程用完，这和输入上下文是否超长是两个独立问题。
    minimum = 16_384 if any(marker in model_id for marker in ("reasoner", "r1", "thinking", "v4", "flash")) else 8_192
    return max(requested, minimum)


class OpenAICompatibleChatModel:
    """调用本地 Ollama 或远端 OpenAI-compatible Chat Completions 服务。"""
    def __init__(self, base_url: str, model: str, api_key: str = "ollama", timeout: float = 600,
                 provider_type: str = "REMOTE", provider_id: str = "openai_compatible"):
        """保存 OpenAI 兼容服务的地址、密钥和模型参数，并初始化异步 HTTP 客户端。"""
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
        """把环境变量来源的数据转换为当前对象，并在边界处整理字段格式。"""
        return cls.from_settings(model_settings.get())

    @classmethod
    def from_settings(cls, settings: dict) -> "OpenAICompatibleChatModel":
        """把设置来源的数据转换为当前对象，并在边界处整理字段格式。"""
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
        """发送一次非流式聊天请求，映射连接/HTTP 错误，解析正文、结束原因和 Token 用量。"""
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
        """对远端模型使用本地价格表估算费用；本地模型不生成费用值。"""
        raise NotImplementedError("Streaming is not enabled for this adapter")

    def get_usage(self) -> Optional[ModelUsage]:
        """以轻量模型列表请求确认当前服务地址和认证信息可用。"""
        return self._last_usage

    def estimate_cost(self, usage: Optional[ModelUsage]) -> Optional[ModelUsage]:
        """按照模型定价表和本次输入/输出 Token 数估算费用；缺少定价时不伪造金额。"""
        if self.provider_type != "REMOTE":
            return usage
        return ProviderPricingConfig.estimate(self.model, usage)

    def health_check(self) -> bool:
        """检查服务配置与连接参数是否完整，为上层调用返回可读的配置诊断结果。"""
        try:
            response = httpx.get(f"{self.base_url}/models", headers={"Authorization": f"Bearer {self.api_key}"},
                                 timeout=min(self.timeout, 8), trust_env=False)
            return response.status_code < 400
        except httpx.RequestError:
            return False
