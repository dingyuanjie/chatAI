"""科研智能体配置基础设施。profile 描述专家职责与能力，registry 负责加载、校验、查询和排序。"""
# 中文模块说明：科研智能体配置与路由模块，负责专家角色定义、配置加载校验、领域匹配和研究工作流约束。

from .base import AgentProfile
from .registry import AgentRegistry

__all__ = ["AgentProfile", "AgentRegistry"]
