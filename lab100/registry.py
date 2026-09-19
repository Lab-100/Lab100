"""Реестр агентов.

Агент — запись с доступом к реальному адаптеру:
- name: уникальный id (совпадает с ключом секции провайдера в конфиге);
- kind: `search` (веб-поиск/скрейпинг) или `llm` (генерация);
- capabilities: компетенции агента (web-search, deep-research, scrape, code, vision, answer);
- priority: вес при выборе (приоритетнее — выше);
- adapter: объект реального провайдера (SearchAdapter | LLMAdapter).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .providers.base import LLMAdapter, SearchAdapter

KIND_SEARCH = "search"
KIND_LLM = "llm"


class RegistryError(Exception):
    """Некорректный реестр или запрос к нему."""


@dataclass
class Agent:
    name: str
    kind: str
    capabilities: frozenset[str] = field(default_factory=frozenset)
    priority: int = 0
    adapter: object | None = None

    def has_capability(self, capability: str) -> bool:
        return capability in self.capabilities


class Registry:
    def __init__(self, agents: list[Agent] | None = None) -> None:
        self._agents: dict[str, Agent] = {}
        self._by_kind: dict[str, list[Agent]] = {KIND_SEARCH: [], KIND_LLM: []}
        for agent in agents or []:
            self.register(agent)

    def register(self, agent: Agent) -> None:
        if agent.name in self._agents:
            raise RegistryError(f"дубликат агента: {agent.name}")
        self._agents[agent.name] = agent
        if agent.kind in self._by_kind:
            self._by_kind[agent.kind].append(agent)

    def agents(self) -> list[Agent]:
        return list(self._agents.values())

    def by_kind(self, kind: str) -> list[Agent]:
        return list(self._by_kind[kind])

    def capable(self, capability: str, kind: str | None = None) -> list[Agent]:
        pool = self.by_kind(kind) if kind else self.agents()
        return [a for a in pool if a.has_capability(capability)]

    def get(self, name: str) -> Agent | None:
        return self._agents.get(name)

    def __len__(self) -> int:
        return len(self._agents)