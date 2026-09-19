"""Выбор исполнителя из пула.

Фильтры:
1. Компетенция обязательна (capability ∈ agent.capabilities).
2. Достаточно квоты (не в cooldown, остаток > резерва).
Сортировка кандидатов: живые — по приоритету, потом по остатку квоты;
«пустые» идут в конец для каскада (переключение после исчерпания первого).
"""

from __future__ import annotations

from lab100.registry import Agent, Registry
from lab100.token_tracker import TokenTracker


class RouterError(Exception):
    """В пуле нет подходящего агента."""


def pick(
    registry: Registry,
    tracker: TokenTracker,
    capability: str,
    kind: str | None = None,
    needed: int | float = 1,
) -> Agent:
    candidates = registry.capable(capability, kind)
    if not candidates:
        raise RouterError(f"нет агентов с компетенцией: {capability}")

    viable = [a for a in candidates if tracker.has_quota(a.name, needed)]
    if not viable:
        raise RouterError(
            f"нет агентов с компетенцией {capability!r} и доступной квотой "
            f"(всего кандидатов: {len(candidates)})"
        )

    return max(viable, key=lambda a: (a.priority, tracker.remaining(a.name)))


def fallback_order(
    registry: Registry,
    tracker: TokenTracker,
    capability: str,
    kind: str | None = None,
    needed: int | float = 1,
) -> list[Agent]:
    candidates = registry.capable(capability, kind)
    alive = [a for a in candidates if tracker.has_quota(a.name, needed)]
    locked = [a for a in candidates if not tracker.has_quota(a.name, needed)]

    def key(a: Agent):
        return (a.priority, tracker.remaining(a.name))

    alive.sort(key=key, reverse=True)
    locked.sort(key=key, reverse=True)
    return alive + locked