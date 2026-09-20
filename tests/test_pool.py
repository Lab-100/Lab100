"""Тесты: registry → tracker → router → orchestrator (на моках адаптеров).

Не трогают сеть. Живые провайдеры проверяются отдельно через CLI.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lab100.orchestrator import PoolOrchestrator  # noqa: E402
from lab100.providers.base import AdapterError, SearchResult  # noqa: E402
from lab100.registry import Agent, Registry  # noqa: E402
from lab100.router import RouterError, fallback_order, pick  # noqa: E402
from lab100.token_tracker import TokenTracker  # noqa: E402


# ── мок-адаптеры ─────────────────────────────────────────────────────────────
class MockSearch:
    name = "firecrawl"
    kind = "search"

    def __init__(self, results=None, ads=None, fail=False):
        self.results = results or [SearchResult(url="https://a.ru", title="A", description="деск")]
        self.ads = ads or 1
        self.fail = fail

    def init(self):
        pass

    def search(self, query, limit=5):
        if self.fail:
            raise AdapterError("mock 429")
        return self.results, self.ads

    def scrape(self, url):
        if self.fail:
            raise AdapterError("mock 429")
        return "# текст страницы", 1


class MockLLM:
    kind = "llm"

    def __init__(self, answer="ответ", fail=False):
        self.answer = answer
        self.fail = fail

    def init(self):
        pass

    def chat(self, prompt, system="", max_tokens=1200):
        if self.fail:
            raise AdapterError("mock llm 429")
        return self.answer, {"model": "mock", "tokens": 42, "credits": 0}


def make_pool():
    reg = Registry()
    reg.register(Agent("firecrawl", "search", frozenset({"web-search", "scrape"}), 50, MockSearch()))
    reg.register(Agent("ollama", "llm", frozenset({"answer", "code"}), 40, MockLLM()))
    return reg, TokenTracker()


# ── registry / router ─────────────────────────────────────────────────────────
def test_capable_by_kind():
    reg, _ = make_pool()
    assert [a.name for a in reg.capable("web-search", "search")] == ["firecrawl"]
    assert [a.name for a in reg.capable("answer", "llm")] == ["ollama"]
    assert reg.capable("vision") == []


def test_pick_prefers_priority():
    reg, tr = make_pool()
    a = pick(reg, tr, "web-search", kind="search")
    assert a.name == "firecrawl"


def test_pick_excludes_locked():
    reg, tr = make_pool()
    tr.ensure("firecrawl").limit = 100
    tr.ensure("firecrawl").used = 95  # выше порога reserve
    assert not tr.has_quota("firecrawl")
    with pytest.raises(RouterError):
        pick(reg, tr, "web-search", kind="search")


def test_fallback_order_puts_locked_last():
    reg, tr = make_pool()
    reg.register(Agent("gemini", "llm", frozenset({"answer"}), 60, MockLLM()))
    tr.ensure("ollama").limit = 100
    tr.cooldown("ollama", 9999)
    order = fallback_order(reg, tr, "answer", kind="llm")
    assert order[0].name == "gemini"
    assert order[-1].name == "ollama"


# ── orchestrator ──────────────────────────────────────────────────────────────
def test_orchestrator_search_records_credits():
    reg, tr = make_pool()
    tr.ensure("firecrawl").limit = 100
    orch = PoolOrchestrator(reg, tr)
    res = orch.run_search("вопрос")
    assert res.ok
    assert res.provider == "firecrawl"
    assert res.sources[0].url == "https://a.ru"
    assert tr.remaining("firecrawl") == 99


def test_orchestrator_search_with_llm():
    reg, tr = make_pool()
    orch = PoolOrchestrator(reg, tr)
    res = orch.run("вопрос", capability="web-search", with_llm=True)
    assert res.ok
    assert res.answer == "ответ"
    assert res.sources and res.sources[0].url == "https://a.ru"
    assert res.provider == "ollama"


def test_orchestrator_fallback_on_429():
    reg = Registry()
    reg.register(Agent("firecrawl", "search", frozenset({"web-search"}), 50, MockSearch(fail=True)))
    reg.register(Agent("ollama", "llm", frozenset({"answer"}), 40, MockLLM()))
    tr = TokenTracker()
    orch = PoolOrchestrator(reg, tr)
    res = orch.run("вопрос", capability="web-search", with_llm=True)
    # поиск упал → задача ошибочна, каскад на другой поисковик недоступен
    assert not res.ok
    assert "mock 429" in (res.error or "")


def test_orchestrator_search_cascades_to_next_provider():
    reg = Registry()
    reg.register(Agent("exa", "search", frozenset({"web-search"}), 45, MockSearch(fail=True)))
    reg.register(Agent("local", "search", frozenset({"web-search"}), 30, MockSearch(ads=0)))
    tr = TokenTracker()
    orch = PoolOrchestrator(reg, tr)
    res = orch.run_search("вопрос")
    assert res.ok
    assert res.provider == "local"
    assert res.attempts == ["exa", "local"]
    assert res.sources[0].url == "https://a.ru"


def test_run_llm_caps():
    reg, tr = make_pool()
    orch = PoolOrchestrator(reg, tr)
    res = orch.run("напиши код", capability="code")
    assert res.ok
    assert res.answer == "ответ"
    res2 = orch.run("ответь", capability="answer")
    assert res2.ok


def test_run_scrape():
    reg, tr = make_pool()
    orch = PoolOrchestrator(reg, tr)
    res = orch.run_scrape("https://a.ru")
    assert res.ok
    assert res.scraped == "# текст страницы"


def test_force_llm_provider():
    reg, tr = make_pool()
    orch = PoolOrchestrator(reg, tr)
    res = orch.run("вопрос", capability="answer", force_llm="ollama")
    assert res.ok
    assert res.provider == "ollama"
    assert res.attempts == ["ollama"]


def test_force_unknown_llm_raises():
    reg, tr = make_pool()
    orch = PoolOrchestrator(reg, tr)
    res = orch.run("вопрос", capability="answer", force_llm="nope")
    assert not res.ok
    assert "nope" in (res.error or "")


# ── конфиг ───────────────────────────────────────────────────────────────────
def test_config_loads_and_build():
    from lab100.cli import build, load_config

    cfg = load_config("config/agents.toml")
    assert "provider" in cfg and "quota" in cfg
    assert cfg["provider"]["firecrawl"]["kind"] == "search"

    orch = build("config/agents.toml")
    assert len(orch.registry.by_kind("search")) >= 1
    assert len(orch.registry.by_kind("llm")) >= 1


def test_config_has_case1_local_providers():
    from lab100.cli import build, load_config

    cfg = load_config("config/agents.toml")
    for name in ("local-browser", "plain-http"):
        assert name in cfg["provider"], name
        assert cfg["provider"][name]["kind"] == "search"
        assert cfg["quota"][name]["window"] == "forever"

    orch = build("config/agents.toml")
    names = {a.name for a in orch.registry.by_kind("search")}
    assert {"local-browser", "plain-http", "firecrawl"} <= names


def test_plain_http_decodes_bing_redirect():
    from lab100.providers.plain_http import PlainHttpScrape

    p = PlainHttpScrape()
    assert (
        p._clean_url(
            "https://www.bing.com/ck/a?!&&p=1&u=a1aHR0cHM6Ly9wdXR0eS5vcmcucnUvZG93bmxvYWQ&ntb=1"
        )
        == "https://putty.org.ru/download"
    )
    # прямой URL остаётся без изменений
    assert p._clean_url("https://putty.org.ru/") == "https://putty.org.ru/"