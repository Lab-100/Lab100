"""Тесты MCP-сервера Lab100 (без сети, на моках).

Проверяют форматирование результата, текст состояния пула и регистрацию
инструментов MCP. Живой поиск не выполняется — его проверяет CLI-смоук.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lab100 import mcp_server  # noqa: E402
from lab100.orchestrator import PoolOrchestrator, TaskResult  # noqa: E402
from lab100.providers.base import SearchResult  # noqa: E402
from lab100.registry import Agent, Registry  # noqa: E402
from lab100.token_tracker import TokenTracker  # noqa: E402


# ── мок-адаптер ──────────────────────────────────────────────────────────────
class MockSearch:
    name = "firecrawl"
    kind = "search"

    def init(self):
        pass

    def search(self, query, limit=5):
        return [SearchResult(url="https://a.ru", title="A", description="деск")], 1

    def scrape(self, url):
        return "# текст страницы", 1


def make_orch() -> PoolOrchestrator:
    reg = Registry()
    reg.register(Agent("firecrawl", "search", frozenset({"web-search", "scrape"}), 50, MockSearch()))
    return PoolOrchestrator(reg, TokenTracker())


# ── format_result ────────────────────────────────────────────────────────────
def test_format_result_sources():
    res = TaskResult(task="q", capability="web-search", ok=True, provider="firecrawl", attempts=["firecrawl"])
    res.sources = [SearchResult(url="https://a.ru", title="A", description="деск", position=1)]
    text = mcp_server.format_result(res)
    assert "источник: firecrawl" in text
    assert "https://a.ru" in text
    assert "1. A" in text
    assert "деск" in text
    assert "ОШИБКА" not in text


def test_format_result_error():
    res = TaskResult(task="q", capability="web-search", ok=False, error="нет доступного агента")
    text = mcp_server.format_result(res)
    assert "ОШИБКА: нет доступного агента" in text


def test_format_result_truncates_scraped():
    res = TaskResult(task="u", capability="scrape", ok=True, scraped="x" * 5000)
    text = mcp_server.format_result(res)
    assert "…(ещё 1000 симв.)" in text


# ── status_text ──────────────────────────────────────────────────────────────
def test_status_text_empty_pool(tmp_path):
    cfg = tmp_path / "agents.toml"
    cfg.write_text("", encoding="utf-8")
    text = mcp_server.status_text(mcp_server.build(str(cfg)))
    assert "Агентов в пуле: 0" in text


def test_status_text_lists_agents():
    text = mcp_server.status_text(make_orch())
    assert "Агентов в пуле: 1" in text
    assert "firecrawl" in text
    assert "web-search" in text


# ── регистрация инструментов ─────────────────────────────────────────────────
def test_tools_registered():
    tools = asyncio.run(mcp_server.mcp.list_tools())
    names = {getattr(tool, "name", None) for tool in tools}
    assert names == {"lab100_search", "lab100_scrape", "lab100_ask", "lab100_status"}
