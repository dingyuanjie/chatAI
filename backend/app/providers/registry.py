"""Factory registry boundary between agents and configured model adapters."""

from typing import Any, Callable, Dict, Optional

from app.model_settings import model_settings
from .models import ChatModel
from .openai_compatible import OpenAICompatibleChatModel


class ModelProviderRegistry:
    """Returns a provider-neutral ChatModel for the current global configuration."""

    def __init__(self):
        self._factories: Dict[str, Callable[[Dict[str, Any]], ChatModel]] = {
            "local": OpenAICompatibleChatModel.from_settings,
            "remote": OpenAICompatibleChatModel.from_settings,
        }

    def register(self, provider_key: str, factory: Callable[[Dict[str, Any]], ChatModel]):
        self._factories[provider_key.strip().lower()] = factory

    def create_research_provider(self, settings: Optional[Dict[str, Any]] = None) -> ChatModel:
        current = settings or model_settings.get()
        key = str(current.get("provider", "local")).strip().lower()
        factory = self._factories.get(key)
        if not factory:
            raise ValueError(f"No model provider adapter registered for: {key}")
        return factory(current)

    def describe_research_provider(self, settings: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        provider = self.create_research_provider(settings)
        return {"provider_id": provider.provider_id, "provider_type": provider.provider_type,
                "model_name": provider.model, "capabilities": provider.capabilities.__dict__}


model_provider_registry = ModelProviderRegistry()
