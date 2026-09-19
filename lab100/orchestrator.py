"""Менеджер пула — точка входа логики.

Принимает задачу, находит подходящего агента по компетенции и квоте,
резервирует квоту до вызова, выполняет задачу (пока через заглушку-адаптер)
и списывает/фиксирует результат. При 429 — переключается на следующего кандидата
и ставит cooldown всем, кто исчерпан.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from lab100.registry import Agent, Registry
from lab100.router import RouterError, fallback_order, pick
from lab100.token_tracker import TokenTracker


class ProviderAdapter:
    """Заглушка реального вызова. Наследники реализуют run(prompt)."""

    def run(self, provider: str, prompt: str) -> str:
        return f"[dry-run] {provider}: {prompt!r}"


@dataclass
class TaskResult:
    task: str
    capability: str
    agent: Agent | None = None
    output: str | None = None
    ok: bool = False
    error: str | None = None
    attempts: list[str] = field(default_factory=list)


class PoolOrchestrator:
    def __init__(
        self,
        registry: Registry,
        tracker: TokenTracker,
        adapter: ProviderAdapter | None = None,
    ) -> None:
        self.registry = registry
        self.tracker = tracker
        self.adapter = adapter or ProviderAdapter()

    def run(
        self,
        task: str,
        capability: str = "web-search",
        needed: int | float = 1,
        dry_run: bool = False,
    ) -> TaskResult:
        result = TaskResult(task=task, capability=capability)
        self.tracker.reset_expired()

        if dry_run:
            try:
                agent = pick(self.registry, self.tracker, capability, needed)
            except RouterError as exc:
                result.error = str(exc)
                return result
            result.agent = agent
            result.ok = True
            result.output = f"[dry-run] выбран агент {agent.name} ({agent.provider})"
            return result

        # Каскад: страхунка на случай rate limit.
        for agent in fallback_order(self.registry, self.tracker, capability, needed):
            result.attempts.append(agent.name)
            if not self.tracker.reserve(agent.provider, needed):
                continue
            try:
                output = self.adapter.run(agent.provider, task)
            except Exception as exc:  # 429/блокировка провайдера
                self.tracker.cooldown(agent.provider, 3600)
                result.error = str(exc)
                continue
            self.tracker.record_used(agent.provider, needed)
            result.agent = agent
            result.output = output
            result.ok = True
            break

        if not result.ok and result.error is None:
            result.error = "все агенты недоступны"
        return result