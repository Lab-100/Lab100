"""Выбор исполнителя из пула.

Два фильтра:
1. Компетенция обязательна (capability ∈ agent.capabilities).
2. Достаточно квоты (не в cooldown, остаток > резерва).
Затем — приоритизация: доступная квота (desc), priority (desc).
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
    needed: int | float = 1,
) -> Agent:
    candidates = registry.capable(capability)
    if not candidates:
        raise RouterError(f"нет агентов с компетенцией: {capability}")

    viable = [
        a
        for a in candidates
        if tracker.has_quota(a.provider, needed)
    ]
    if not viable:
        raise RouterError(
            f"нет агентов с компетенцией {capability!r} и доступной квотой "
            f"(всего кандидатов: {len(candidates)})"
        )

    best = max(viable, key=lambda a: (tracker.remaining(a.provider), a.priority))
    return best


def fallback_order(
    registry: Registry,
    tracker: TokenTracker,
    capability: str,
    needed: int | float = 1,
) -> list[Agent]:
    """Упорядоченный список кандидатов для каскада переключения (скорее живые)."""
    candidates = registry.capable(capability)
    locked = [a for a in candidates if a in [x for x in candidates if not tracker.has_quota(x.provider, needed)]]
    alive = [a for a in candidates if tracker.has_quota(a.provider, needed)]
    alive.sort(key=lambda a: (tracker.remaining(a.provider), a.priority), reverse=True)
    locked.sort(key=lambda a: (tracker.remaining(a.provider), a.priority), reverse=True)
    return alive + locked