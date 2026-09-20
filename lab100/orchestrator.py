"""Менеджер пула — реальный конвейер выполнения задач.

Задача + capability → компановка шагов:
- web-search / deep-research (kind=search): Firecrawl поиск → список результатов;
- answer / code (kind=llm): генерация через LLM по контексту найденного;
- scrape: извлечение markdown по URL.

Роутер выбирает агента по компетенции и доступной квоте, на каждый вызов
списывает фактический расход (creditsUsed / tokens). При ошибке провайдера —
каскад на следующего кандидата с cooldown исчерпанного.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .providers.base import AdapterError, LLMAdapter, SearchAdapter, SearchResult
from .registry import Agent, KIND_LLM, KIND_SEARCH, Registry
from .router import RouterError, fallback_order
from .token_tracker import TokenTracker

SEARCH_CAPS = {"web-search", "deep-research", "scrape"}
LLM_CAPS = {"answer", "code"}


@dataclass
class TaskResult:
    task: str
    capability: str
    ok: bool = False
    answer: str | None = None
    sources: list[SearchResult] = field(default_factory=list)
    scraped: str = ""
    provider: str | None = None
    metrics: dict = field(default_factory=dict)
    attempts: list[str] = field(default_factory=list)
    error: str | None = None


class PoolOrchestrator:
    def __init__(self, registry: Registry, tracker: TokenTracker) -> None:
        self.registry = registry
        self.tracker = tracker

    # ── выбор исполнителя ────────────────────────────────────────────────────
    def _pick(self, kind: str, capability: str, force: str | None = None) -> Agent:
        for agent in fallback_order(self.registry, self.tracker, capability, kind=kind, force=force):
            if self._reserve(agent, 1):
                return agent
        raise RouterError(f"нет доступного агента ({kind}/{capability})")

    def _reserve(self, agent: Agent, amount: float) -> bool:
        """Проверка доступности квоты (резерв списывается по факту в record)."""
        if agent.adapter is None:
            return False
        return self.tracker.has_quota(agent.name, amount)

    # ── поиск ────────────────────────────────────────────────────────────────
    def run_search(self, query: str, limit: int = 5, capability: str = "web-search", force: str | None = None) -> TaskResult:
        result = TaskResult(task=query, capability=capability)
        self.tracker.reset_expired()
        try:
            order = fallback_order(self.registry, self.tracker, capability, kind=KIND_SEARCH, force=force)
        except RouterError as exc:
            result.error = str(exc)
            return result
        for agent in order:
            if not self._reserve(agent, 1):
                continue
            adapter: SearchAdapter = agent.adapter
            result.attempts.append(agent.name)
            try:
                found, spent = adapter.search(query, limit)
            except AdapterError as exc:
                self._on_failure(agent, exc, result)
                continue
            self.tracker.record(agent.name, spent)
            result.sources = found
            result.provider = agent.name
            result.ok = True
            result.metrics.update({"credits": spent, "results": len(found)})
            return result
        return result

    def run_scrape(self, url: str, capability: str = "scrape", force: str | None = None) -> TaskResult:
        result = TaskResult(task=url, capability=capability)
        self.tracker.reset_expired()
        try:
            order = fallback_order(self.registry, self.tracker, capability, kind=KIND_SEARCH, force=force)
        except RouterError as exc:
            result.error = str(exc)
            return result
        for agent in order:
            if not self._reserve(agent, 1):
                continue
            adapter: SearchAdapter = agent.adapter
            result.attempts.append(agent.name)
            try:
                markdown, spent = adapter.scrape(url)
            except AdapterError as exc:
                self._on_failure(agent, exc, result)
                continue
            self.tracker.record(agent.name, spent)
            result.scraped = markdown
            result.provider = agent.name
            result.ok = True
            result.metrics.update({"credits": spent, "chars": len(markdown)})
            return result
        return result

    # ── генерация через LLM ──────────────────────────────────────────────────
    def run_llm(
        self,
        prompt: str,
        capability: str = "answer",
        system: str = "",
        context: str = "",
        max_tokens: int = 1200,
        force: str | None = None,
    ) -> TaskResult:
        result = TaskResult(task=prompt[:120], capability=capability)
        self.tracker.reset_expired()
        full_prompt = (context + "\n\n" + prompt) if context else prompt
        try:
            order = fallback_order(self.registry, self.tracker, capability, kind=KIND_LLM, force=force)
        except RouterError as exc:
            result.error = str(exc)
            return result
        for agent in order:
            if not self._reserve(agent, 1):
                continue
            adapter: LLMAdapter = agent.adapter
            result.attempts.append(agent.name)
            try:
                answer, metrics = adapter.chat(full_prompt, system=system, max_tokens=max_tokens)
            except AdapterError as exc:
                self._on_failure(agent, exc, result)
                continue
            self.tracker.record(agent.name, metrics.get("tokens", 0))
            result.answer = answer
            result.provider = agent.name
            result.ok = True
            result.metrics.update(metrics)
            return result
        return result

    # ── высокоуровневая задача ───────────────────────────────────────────────
    def run(
        self,
        task: str,
        capability: str = "web-search",
        limit: int = 5,
        with_llm: bool = True,
        system: str = "",
        force_llm: str | None = None,
        force_search: str | None = None,
    ) -> TaskResult:
        if capability in LLM_CAPS:
            return self.run_llm(task, capability=capability, system=system, max_tokens=1400, force=force_llm)
        if capability == "scrape":
            return self.run_scrape(task, force=force_search)
        # web-search / deep-research: ищем, затем при желании оформляем ответом LLM
        result = self.run_search(task, limit=limit, capability=capability, force=force_search)
        if not result.ok:
            return result
        if with_llm:
            context = self._format_context(result, task, limit)
            llm = self.run_llm(
                "На основе приведённого контекста дай точный и краткий ответ на вопрос. "
                "Не выдумывай фактов: если в контексте нет ответа — так и скажи. "
                "НЕ упоминай технологии, которые не описаны в контексте. "
                "В конце перечисли источники (URL).",
                capability="answer",
                system=system,
                context=context,
                max_tokens=1000,
                force=force_llm,
            )
            if llm.ok:
                result.answer = llm.answer
                result.provider = llm.provider
                result.metrics.update(llm.metrics)
                result.attempts += llm.attempts
            else:
                result.error = result.error or llm.error
        return result

    @staticmethod
    def _format_context(result: TaskResult, task: str, limit: int) -> str:
        lines = [f"Вопрос/задача: {task}", f"Найдено результатов: {len(result.sources)}"] if result.sources else [f"Задача: {task}", "Результатов нет."]
        for src in result.sources[:limit]:
            lines.append(f"\n[{src.position}] {src.title}\nURL: {src.url}\n{src.description[:1500]}")
        return "\n".join(lines)

    def _on_failure(self, agent: Agent, exc: Exception, result: TaskResult) -> None:
        self.tracker.cooldown(agent.name, 600)
        result.error = str(exc)
        if isinstance(exc, AdapterError):
            result.error = f"{agent.name}: {exc}"