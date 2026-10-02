"""Provider-neutral chat model contract."""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ModelResponse:
    text: str
    model: str
    finish_reason: str = ""


class ChatModel(Protocol):
    def generate(self, system: str, prompt: str, *, temperature: float, max_tokens: int) -> ModelResponse: ...

