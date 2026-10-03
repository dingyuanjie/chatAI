"""Provider-neutral chat model contract."""

from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, Literal, Optional, Protocol


ProviderType = Literal["LOCAL", "REMOTE", "HYBRID"]


@dataclass(frozen=True)
class ModelCapabilities:
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
        return self.provider_type == "LOCAL"

    @property
    def is_remote(self) -> bool:
        return self.provider_type == "REMOTE"


@dataclass(frozen=True)
class ModelUsage:
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    cached_input_tokens: Optional[int] = None
    estimated_cost: Optional[float] = None
    currency: Optional[str] = None

    @classmethod
    def from_payload(cls, value: Dict[str, Any] | None) -> Optional["ModelUsage"]:
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
    def __init__(self, message: str, reason_code: str, *, status_code: Optional[int] = None):
        super().__init__(message)
        self.reason_code = reason_code
        self.status_code = status_code


@dataclass(frozen=True)
class ModelResponse:
    text: str
    model: str
    finish_reason: str = ""
    provider_id: str = ""
    provider_type: ProviderType = "REMOTE"
    usage: Optional[ModelUsage] = None
    capabilities: ModelCapabilities = field(default_factory=lambda: ModelCapabilities("REMOTE"))


class ChatModel(Protocol):
    provider_id: str
    provider_type: ProviderType
    model: str
    capabilities: ModelCapabilities

    def generate(self, system: str, prompt: str, *, temperature: float, max_tokens: int) -> ModelResponse: ...


class ModelProvider(ChatModel, Protocol):
    def stream(self, system: str, prompt: str, *, temperature: float, max_tokens: int) -> Iterator[str]: ...
    def get_usage(self) -> Optional[ModelUsage]: ...
    def health_check(self) -> bool: ...
    def estimate_cost(self, usage: Optional[ModelUsage]) -> Optional[ModelUsage]: ...
