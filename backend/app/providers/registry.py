"""模型提供方注册表。

注册表把“全局设置选了哪个后端”与“聊天/科研怎样调用模型”隔离开。当前本地
Ollama 和远端 OpenAI-compatible 服务复用同一适配器工厂；未来新增提供方时，
可以注册自己的工厂，而不需要让科研引擎直接判断供应商名称和 HTTP 格式。
"""
# 中文模块说明：模型提供方抽象与适配层，统一模型请求、能力描述、用量解析、费用估算和后端工厂选择，避免业务逻辑绑定单一供应商。

from typing import Any, Callable, Dict, Optional

from app.model_settings import model_settings
from .models import ChatModel
from .openai_compatible import OpenAICompatibleChatModel


class ModelProviderRegistry:
    """读取全局模型配置并返回统一 ChatModel 接口；业务调用方不需要根据本地/远端分别构造客户端。"""

    def __init__(self):
        """建立内置提供方工厂；配置读取仍由统一模型设置对象负责。"""
        self._factories: Dict[str, Callable[[Dict[str, Any]], ChatModel]] = {
            "local": OpenAICompatibleChatModel.from_settings,
            "remote": OpenAICompatibleChatModel.from_settings,
        }

    def register(self, provider_key: str, factory: Callable[[Dict[str, Any]], ChatModel]):
        """注册或替换一个提供方工厂，键名统一转为小写并去除首尾空白。"""
        self._factories[provider_key.strip().lower()] = factory

    def create_research_provider(self, settings: Optional[Dict[str, Any]] = None) -> ChatModel:
        """根据显式设置或当前全局设置构造科研可用的模型客户端。

        每次调用都读取当前设置，因此持续科研运行期间切换本地/远端模型后，
        下一次新请求会使用新配置；调用记录仍会保存每次实际使用的模型身份。
        """
        current = settings or model_settings.get()
        key = str(current.get("provider", "local")).strip().lower()
        factory = self._factories.get(key)
        if not factory:
            raise ValueError(f"No model provider adapter registered for: {key}")
        return factory(current)

    def describe_research_provider(self, settings: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """返回前端可展示的模型身份与能力元数据，不包含 API 密钥。"""
        provider = self.create_research_provider(settings)
        return {"provider_id": provider.provider_id, "provider_type": provider.provider_type,
                "model_name": provider.model, "capabilities": provider.capabilities.__dict__}


# 全应用共享注册表；提供方的密钥和地址由 model_settings 单独持久化管理。
model_provider_registry = ModelProviderRegistry()
