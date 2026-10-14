"""MCP-сервер Lab100 (stdio) — пул поиска как набор инструментов для opencode.

Назначение: единая точка подмены (ротации) поисковых провайдеров. opencode
вызывает инструменты как обычный MCP-сервер, а внутри работает тот же каскад
провайдеров с cooldown, что и в CLI — аналогично агрегатору multitool.

Запуск:
    python -m lab100.mcp_server --config config/agents.toml
    lab100-mcp --config <путь к agents.toml>

Конфиг ищется так же, как в CLI: явный путь → переменная LAB100_CONFIG →
config/agents.toml в рабочем каталоге → %LOCALAPPDATA%\\lab100\\agents.toml.

ВАЖНО: протокол MCP идёт по stdio, поэтому в stdout ничего печатать нельзя —
всё отладочное пишется в stderr.
"""

from __future__ import annotations

import argparse
import os
import sys

from mcp.server.fastmcp import FastMCP

from .cli import _utf8, build
from .orchestrator import PoolOrchestrator, TaskResult

MAX_SCRAPE_CHARS = 4000

mcp = FastMCP("lab100")

# Оркестратор строится один раз при первом обращении к любому инструменту.
_orchestrator: PoolOrchestrator | None = None
_config_path: str = ""


def _config_candidates() -> list[str]:
    """Кандидаты пути к agents.toml для MCP-процесса.

    MCP-сервер запускает opencode с произвольным рабочим каталогом, поэтому
    одного cwd мало: сначала явный путь/LAB100_CONFIG, затем config/agents.toml
    рядом с пакетом (запуск из клона).
    """
    candidates: list[str] = []
    explicit = _config_path or os.environ.get("LAB100_CONFIG", "")
    if explicit:
        candidates.append(explicit)
    repo_config = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "agents.toml")
    if os.path.isfile(repo_config):
        candidates.append(repo_config)
    return candidates


def _get_orchestrator() -> PoolOrchestrator:
    """Ленивая сборка пула: один раз, при первом вызове инструмента."""
    global _orchestrator
    if _orchestrator is None:
        # Первый существующий кандидат; иначе пусто — пусть cli.build ищет сам.
        config_path = next((c for c in _config_candidates() if os.path.isfile(c)), "")
        _orchestrator = build(config_path)
        print(f"lab100-mcp: пул собран из {config_path or '<поиск конфига cli>'}", file=sys.stderr)
    return _orchestrator


def format_result(res: TaskResult) -> str:
    """Текст результата задачи в markdown (без сети и MCP — чисто по TaskResult)."""
    lines = [f"источник: {res.provider or '-'} | попытки: {', '.join(res.attempts) or '-'}"]
    if res.metrics:
        lines.append("метрики: " + ", ".join(f"{k}={v}" for k, v in res.metrics.items()))
    if res.sources:
        lines.append("")
        lines.append("источники:")
        for src in res.sources[:10]:
            lines.append(f"{src.position}. {src.title}")
            lines.append(f"   {src.url}")
            if src.description:
                lines.append(f"   {src.description}")
    if res.answer:
        lines.append("")
        lines.append("ответ:")
        lines.append(res.answer)
    if res.scraped:
        body = res.scraped
        if len(body) > MAX_SCRAPE_CHARS:
            body = body[:MAX_SCRAPE_CHARS] + f"\n…(ещё {len(res.scraped) - MAX_SCRAPE_CHARS} симв.)"
        lines.append("")
        lines.append("markdown:")
        lines.append(body)
    if not res.ok:
        lines.append("")
        lines.append(f"ОШИБКА: {res.error}")
    return "\n".join(lines)


def status_text(orch: PoolOrchestrator) -> str:
    """Состояние пула: агенты, квоты, причины отключения адаптеров (по образцу cmd_status)."""
    lines = [f"Агентов в пуле: {len(orch.registry)}"]
    for agent in orch.registry.agents():
        caps = ", ".join(sorted(agent.capabilities)) or "-"
        rem = orch.tracker.remaining(agent.name)
        rem_str = f"{rem:,.0f}" if rem != float("inf") else "∞"
        lines.append(f"[{agent.kind:<6}] {agent.name:<16} квота: {rem_str:>10} | {caps}")
    for note in getattr(orch, "_notes", []):
        lines.append(note)
    return "\n".join(lines)


@mcp.tool()
def lab100_search(query: str, limit: int = 5, capability: str = "web-search") -> str:
    """Веб-поиск через пул Lab100 с ротацией провайдеров.

    Ищет в вебе, автоматически переключаясь между провайдерами (Firecrawl,
    Exa, local-browser, plain-http, yandex-search) при ошибках, лимитах или
    блокировках. Возвращает список источников с заголовками, URL и описаниями.
    """
    return format_result(_get_orchestrator().run_search(query, limit=limit, capability=capability))


@mcp.tool()
def lab100_scrape(url: str) -> str:
    """Извлекает содержимое веб-страницы в markdown через пул Lab100.

    Пробует провайдеров по очереди (каскад с cooldown), возвращает текст
    страницы (обрезан до 4000 символов).
    """
    return format_result(_get_orchestrator().run_scrape(url))


@mcp.tool()
def lab100_ask(question: str, limit: int = 5, capability: str = "answer") -> str:
    """Отвечает на вопрос: веб-поиск через пул Lab100 + генерация ответа LLM.

    Собирает источники и оформляет краткий ответ локальной или облачной моделью
    (тоже с ротацией). capability: answer, code, web-search или deep-research.
    """
    return format_result(
        _get_orchestrator().run(question, capability=capability, limit=limit, with_llm=True)
    )


@mcp.tool()
def lab100_status() -> str:
    """Состояние пула Lab100: агенты, их квоты и причины отключения адаптеров.

    Полезно, чтобы понять, какие провайдеры сейчас доступны и почему часть
    отключена (нет ключа, лимит, cooldown).
    """
    return status_text(_get_orchestrator())


def main(argv: list[str] | None = None) -> None:
    global _config_path
    parser = argparse.ArgumentParser(prog="lab100-mcp", description="MCP-сервер пула Lab100 (stdio)")
    parser.add_argument(
        "--config",
        default="",
        help="путь к agents.toml (по умолчанию: LAB100_CONFIG, затем config/agents.toml "
             "и %%LOCALAPPDATA%%\\lab100\\agents.toml)",
    )
    args = parser.parse_args(argv)
    _utf8()
    _config_path = args.config
    mcp.run()  # stdio — всё общение идёт через протокол MCP, stdout не трогаем


if __name__ == "__main__":
    main()
