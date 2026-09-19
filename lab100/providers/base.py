"""Интерфейсы адаптеров провайдеров.

Два рода адаптеров:
- SearchAdapter — веб-поиск и скрейпинг (Firecrawl).
- LLMAdapter — генерация текста (Ollama, Gemini).

Каждый адаптер сообщает фактический расход (кредиты/токены), который оркестратор
списывает со счётчика квоты.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SearchResult:
    url: str
    title: str = ""
    description: str = ""
    position: int = 0
    extra: dict = field(default_factory=dict)


class AdapterError(Exception):
    """Провайдер вернул ошибку (429, таймаут, сеть)."""


class SearchAdapter:
    kind = "search"
    name: str = "base"

    def init(self) -> None:
        """Валидирует конфигурацию; RuntimeError — если ключ/доступ отсутствует."""

    def search(self, query: str, limit: int = 5) -> tuple[list[SearchResult], int]:
        """Возвращает (результаты, потраченные кредиты)."""
        raise NotImplementedError

    def scrape(self, url: str) -> tuple[str, int]:
        """Возвращает (markdown, потраченные кредиты)."""
        raise NotImplementedError


class LLMAdapter:
    kind = "llm"
    name: str = "base"

    def init(self) -> None:
        raise NotImplementedError

    def chat(
        self, prompt: str, system: str = "", max_tokens: int = 1200
    ) -> tuple[str, dict]:
        """Возвращает (ответ, метрики расхода: tokens/credits)."""
        raise NotImplementedError