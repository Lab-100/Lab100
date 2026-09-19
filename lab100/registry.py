"""Реестр агентов.

Агент — запись с компетенциями и политикой квоты:
- name: уникальный id;
- provider: к какому пулу ключей относится (и ключ для token_tracker);
- capabilities: множество компетенций (web-search, deep-research, scrape, code, vision);
- priority: вес для router (приоритет выбора при равной квоте).
"""

from __future__ import annotations

from dataclasses import dataclass, field


class RegistryError(Exception):
    """Некорректный реестр или запрос к нему."""


@dataclass(frozen=True)
class Agent:
    name: str
    provider: str
    capabilities: frozenset[str] = field(default_factory=frozenset)
    priority: int = 0

    def has_capability(self, capability: str) -> bool:
        return capability in self.capabilities


class Registry:
    def __init__(self, agents: list[Agent] | None = None) -> None:
        self._agents: dict[str, Agent] = {}
        for agent in agents or []:
            self.register(agent)

    def register(self, agent: Agent) -> None:
        if agent.name in self._agents:
            raise RegistryError(f"дубликат агента: {agent.name}")
        self._agents[agent.name] = agent

    @classmethod
    def from_config(cls, providers: dict) -> "Registry":
        """Строит реестр из секций провайдеров TOML-конфига.

        Ожидается формат:
            [provider.<name>]
            capabilities = ["web-search", ...]
            priority = 10
        Если секции провайдера нет — вместо неё создаётся один агент `provider`.
        """
        registry = cls()
        for provider_name, section in (providers or {}).items():
            capabilities = frozenset(section.get("capabilities", []))
            priority = int(section.get("priority", 0))
            registry.register(Agent(provider_name, provider_name, capabilities, priority))
        return registry

    def agents(self) -> list[Agent]:
        return list(self._agents.values())

    def capable(self, capability: str) -> list[Agent]:
        return [a for a in self._agents.values() if a.has_capability(capability)]

    def get(self, name: str) -> Agent | None:
        return self._agents.get(name)

    def __len__(self) -> int:
        return len(self._agents)