"""模型调用的提供方无关数据契约。业务层只依赖统一的响应、能力、用量和错误类型，不直接处理不同服务商的 HTTP 返回结构。"""
# 中文模块说明：模型提供方抽象与适配层，统一模型请求、能力描述、用量解析、费用估算和后端工厂选择，避免业务逻辑绑定单一供应商。

from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, Literal, Optional, Protocol


ProviderType = Literal["LOCAL", "REMOTE", "HYBRID"]


@dataclass(frozen=True)
class ModelCapabilities:
    """声明模型后端能力，供前端配置、调用策略和运行监控显示。

    能力标记描述适配器支持的行为；context_window/max_output_tokens 表示已知限制，
    未知时使用 None，避免把供应商未公开的值伪装成确定事实。
    """
    provider_type: ProviderType
    supports_streaming: bool = False
    supports_usage_reporting: bool = False
    supports_cost_reporting: bool = False
    supports_embeddings: bool = False
    supports_tool_calling: bool = False
    supports_json_output: bool = False
    context_window: Optional[int] = None
    max_output_tokens: Optional[int] = None

    @property
    def is_local(self) -> bool:
        """根据当前对象的状态判断是否满足“islocal”条件，并返回布尔结果。"""
        return self.provider_type == "LOCAL"

    @property
    def is_remote(self) -> bool:
        """根据当前对象的状态判断是否满足“isremote”条件，并返回布尔结果。"""
        return self.provider_type == "REMOTE"


@dataclass(frozen=True)
class ModelUsage:
    """一轮模型请求的 Token 与费用用量。

    供应商未返回的字段保持 None；费用是可选估算值，不等同于账单结算值。
    """
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    cached_input_tokens: Optional[int] = None
    estimated_cost: Optional[float] = None
    currency: Optional[str] = None

    @classmethod
    def from_payload(cls, value: Dict[str, Any] | None) -> Optional["ModelUsage"]:
        """兼容 OpenAI 风格的 prompt/completion 字段和部分服务的 input/output 别名。"""
        if not isinstance(value, dict):
            return None
        prompt = value.get("prompt_tokens", value.get("input_tokens"))
        completion = value.get("completion_tokens", value.get("output_tokens"))
        total = value.get("total_tokens")
        details = value.get("prompt_tokens_details") or value.get("input_tokens_details") or {}
        cached = value.get("prompt_cache_hit_tokens", details.get("cached_tokens"))
        if prompt is None and completion is None and total is None:
            return None
        input_tokens = int(prompt) if prompt is not None else None
        output_tokens = int(completion) if completion is not None else None
        return cls(input_tokens=input_tokens, output_tokens=output_tokens,
                   total_tokens=int(total) if total is not None else ((input_tokens or 0) + (output_tokens or 0)),
                   cached_input_tokens=int(cached) if cached is not None else None)


class ModelProviderError(RuntimeError):
    """含稳定原因代码的模型错误，供连续科研策略而不是文案字符串做决策。"""

    def __init__(self, message: str, reason_code: str, *, status_code: Optional[int] = None):
        """校验远端提供方配置并设置安全默认值；敏感密钥不应通过公开序列化返回给前端。"""
        super().__init__(message)
        self.reason_code = reason_code
        self.status_code = status_code


@dataclass(frozen=True)
class ModelResponse:
    """标准化模型响应；业务层从这里读取正文、来源身份、结束原因和用量。"""
    text: str
    model: str
    finish_reason: str = ""
    provider_id: str = ""
    provider_type: ProviderType = "REMOTE"
    usage: Optional[ModelUsage] = None
    capabilities: ModelCapabilities = field(default_factory=lambda: ModelCapabilities("REMOTE"))


class ChatModel(Protocol):
    """聊天生成适配器的最小调用协议。"""
    provider_id: str
    provider_type: ProviderType
    model: str
    capabilities: ModelCapabilities

    def generate(self, system: str, prompt: str, *, temperature: float, max_tokens: int) -> ModelResponse: ...


class ModelProvider(ChatModel, Protocol):
    """包含流式、健康检查和资源估算操作的完整模型提供方协议。"""
    def stream(self, system: str, prompt: str, *, temperature: float, max_tokens: int) -> Iterator[str]: ...
    def get_usage(self) -> Optional[ModelUsage]: ...
    def health_check(self) -> bool: ...
    def estimate_cost(self, usage: Optional[ModelUsage]) -> Optional[ModelUsage]: ...
