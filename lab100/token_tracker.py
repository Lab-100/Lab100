"""Учёт квот провайдеров.

Отслеживает остатки бесплатных токенов/вызовов по ключу провайдера, окно
пополнения и cooldown после rate limit (429).

Квоты задаются в конфиге в формате:
    [quota.<provider>]
    window = "day" | "month" | "forever"
    limit = 1500                # объём квоты в окне; отсутствует = бесконечная
    reserve_portions = 0.1      # доля окна, ниже которой агент считается «пустым»
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


class Window:
    """Окно пополнения квоты."""

    DAY = "day"
    MONTH = "month"
    FOREVER = "forever"

    @staticmethod
    def seconds(window: str) -> float | None:
        if window == Window.DAY:
            return 24 * 3600
        if window == Window.MONTH:
            return 30 * 24 * 3600
        if window == Window.FOREVER:
            return None
        raise ValueError(f"неизвестное окно: {window}")


@dataclass
class Quota:
    provider: str
    window: str = Window.DAY
    limit: int | None = None
    reserve_portions: float = 0.1
    used: int = 0
    window_started_at: float = field(default_factory=time.time)
    locked_until: float = 0.0


class TokenTracker:
    def __init__(self) -> None:
        self._quotas: dict[str, Quota] = {}

    @classmethod
    def from_config(cls, quota_config: dict) -> "TokenTracker":
        tracker = cls()
        for provider, section in (quota_config or {}).items():
            limit = section.get("limit")
            tracker._quotas[provider] = Quota(
                provider=provider,
                window=str(section.get("window", Window.DAY)),
                limit=int(limit) if limit is not None else None,
                reserve_portions=float(section.get("reserve_portions", 0.1)),
            )
        return tracker

    def ensure(self, provider: str) -> Quota:
        if provider not in self._quotas:
            self._quotas[provider] = Quota(provider=provider)
        return self._quotas[provider]

    def remaining(self, provider: str) -> float:
        q = self.ensure(provider)
        if q.limit is None:
            return float("inf")
        return float(q.limit) - q.used

    def has_quota(self, provider: str, needed: int | float = 1) -> bool:
        q = self.ensure(provider)
        if time.time() < q.locked_until:
            return False
        if q.limit is None:
            return True
        threshold = q.limit * q.reserve_portions
        return (float(q.limit) - q.used) >= min(needed, threshold) if needed else True

    def reserve(self, provider: str, amount: float = 1) -> bool:
        """Проактивно резервирует квоту. Возвращает False, если остатка мало."""
        q = self.ensure(provider)
        if time.time() < q.locked_until:
            return False
        if q.limit is None:
            return True
        if float(q.limit) - q.used < amount:
            return False
        q.used += int(amount)
        return True

    def record_used(self, provider: str, amount: float) -> None:
        q = self.ensure(provider)
        if q.limit is not None:
            q.used = min(int(q.used + amount), q.limit)

    def cooldown(self, provider: str, seconds: float) -> None:
        q = self.ensure(provider)
        q.locked_until = time.time() + seconds

    def reset_expired(self) -> None:
        """Сбрасывает использованное, если окно пополнения закончилось."""
        now = time.time()
        for q in self._quotas.values():
            period = Window.seconds(q.window)
            if period is not None and (now - q.window_started_at) >= period:
                elapsed = int((now - q.window_started_at) // period)
                q.window_started_at += elapsed * period
                q.used = 0
                q.locked_until = 0.0

    def snapshot(self) -> dict[str, dict]:
        return {
            p: {
                "remaining": self.remaining(p),
                "window": q.window,
                "limit": q.limit,
                "locked_until": q.locked_until,
            }
            for p, q in self._quotas.items()
        }