"""科研智能体目录管理器：从 profiles 目录加载 JSON 档案，验证唯一标识并提供按领域、能力查询的入口。"""
# 中文模块说明：科研智能体配置与路由模块，负责专家角色定义、配置加载校验、领域匹配和研究工作流约束。

import json
import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from .base import AgentProfile


class AgentRegistry:
    """维护科研智能体档案的内存索引，提供按唯一 ID、研究领域和能力标签检索专家的统一入口。"""
    def __init__(self, profiles: Optional[Iterable[AgentProfile]] = None):
        """创建空的智能体索引，并把传入的档案逐一校验后注册。"""
        self._profiles: Dict[str, AgentProfile] = {}
        for profile in profiles or ():
            self.register_agent(profile)

    @classmethod
    def from_directory(cls, directory: Path) -> "AgentRegistry":
        """读取目录下按文件名排序的 JSON 档案；解析错误会指出具体文件，避免部分配置静默丢失。"""
        registry = cls()
        for path in sorted(directory.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError(f"Could not load agent profile {path.name}: {exc}") from exc
            profile = AgentProfile.from_dict(data)
            registry.register_agent(profile)
        return registry

    def register_agent(self, profile: AgentProfile) -> None:
        """验证智能体 ID 符合稳定的 URL/配置命名规则且没有重复，然后加入索引。"""
        if not re.fullmatch(r"[a-z][a-z0-9_-]{1,63}", profile.id):
            raise ValueError(f"Invalid agent id: {profile.id!r}")
        if profile.id in self._profiles:
            raise ValueError(f"Agent id is already registered: {profile.id}")
        self._profiles[profile.id] = profile

    def get_agent(self, agent_id: str) -> Optional[AgentProfile]:
        """按唯一 ID 查找智能体；未注册时返回 None，交由调用方决定如何处理。"""
        return self._profiles.get(agent_id)

    def list_agents(self, include_internal: bool = False) -> List[AgentProfile]:
        """按优先级降序、ID 升序列出档案；默认隐藏只供系统内部编排使用的专家。"""
        profiles = self._profiles.values()
        if not include_internal:
            profiles = (profile for profile in profiles if profile.selectable)
        return sorted(profiles, key=lambda profile: (-profile.priority, profile.id))

    def find_agents_by_domain(self, domain: str) -> List[AgentProfile]:
        """忽略大小写匹配研究领域标签，并仅从当前可选择的专家中返回结果。"""
        key = domain.casefold()
        return [profile for profile in self.list_agents() if any(item.casefold() == key for item in profile.domains)]

    def find_agents_by_capability(self, capability: str) -> List[AgentProfile]:
        """忽略大小写匹配技能标签，供科研路由器按任务能力筛选专家。"""
        key = capability.casefold()
        return [profile for profile in self.list_agents() if any(item.casefold() == key for item in profile.skills)]
