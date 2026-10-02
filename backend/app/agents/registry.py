"""Load, validate, and discover research-agent profiles."""

import json
import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from .base import AgentProfile


class AgentRegistry:
    def __init__(self, profiles: Optional[Iterable[AgentProfile]] = None):
        self._profiles: Dict[str, AgentProfile] = {}
        for profile in profiles or ():
            self.register_agent(profile)

    @classmethod
    def from_directory(cls, directory: Path) -> "AgentRegistry":
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
        if not re.fullmatch(r"[a-z][a-z0-9_-]{1,63}", profile.id):
            raise ValueError(f"Invalid agent id: {profile.id!r}")
        if profile.id in self._profiles:
            raise ValueError(f"Agent id is already registered: {profile.id}")
        self._profiles[profile.id] = profile

    def get_agent(self, agent_id: str) -> Optional[AgentProfile]:
        return self._profiles.get(agent_id)

    def list_agents(self, include_internal: bool = False) -> List[AgentProfile]:
        profiles = self._profiles.values()
        if not include_internal:
            profiles = (profile for profile in profiles if profile.selectable)
        return sorted(profiles, key=lambda profile: (-profile.priority, profile.id))

    def find_agents_by_domain(self, domain: str) -> List[AgentProfile]:
        key = domain.casefold()
        return [profile for profile in self.list_agents() if any(item.casefold() == key for item in profile.domains)]

    def find_agents_by_capability(self, capability: str) -> List[AgentProfile]:
        key = capability.casefold()
        return [profile for profile in self.list_agents() if any(item.casefold() == key for item in profile.skills)]
