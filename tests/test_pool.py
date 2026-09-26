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
    notes = getattr(orch, "_notes", [])
    # local-browser/plain-http обязательны в конфиге; в пуле local-browser может быть
    # корректно исключён, если node-драйвер (127.0.0.1:8123) не поднят в окружении.
    assert "plain-http" in names
    assert "firecrawl" in names
    assert "local-browser" in names or any("local-browser" in n for n in notes)


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


# ── docker-agent (Гордон как член группы перебора LLM) ───────────────────────
def test_docker_agent_adapter_uses_docker_cli(monkeypatch):
    from lab100.providers.docker_agent import DockerAgentLLM

    called = {}

    class FakeProc:
        returncode = 0
        stdout = b"\xd0\xbe\xd1\x82\xd0\xb2\xd0\xb5\xd1\x82\x20\xd0\xb3\xd0\xbe\xd1\x80\xd0\xb4\xd0\xbe\xd0\xbd\xd0\xb0"  # «ответ гордона»
        stderr = b""

    def fake_run(cmd, capture_output=True, timeout=1):
        called["cmd"] = cmd
        return FakeProc()

    monkeypatch.setattr("lab100.providers.docker_agent.subprocess.run", fake_run)
    adapter = DockerAgentLLM(agent_file=r"C:\x\gordon.yaml", model="local", docker_exe="docker")
    adapter.init = lambda: None  # наличие плагина проверяется вызовом docker (не в unit-тесте)
    answer, metrics = adapter.chat("вопрос")
    assert answer == "ответ гордона"
    assert called["cmd"][1] == "agent"
    assert called["cmd"][3] == r"C:\x\gordon.yaml"
    assert called["cmd"][5] == "local"
    assert metrics["credits"] == 0


def test_docker_agent_init_raises_without_plugin(monkeypatch):
    import pytest as _pytest

    from lab100.providers import docker_agent as da
    from lab100.providers.docker_agent import DockerAgentLLM

    monkeypatch.setattr(da, "_has_agent_plugin", lambda exe: False)
    adapter = DockerAgentLLM(agent_file=r"C:\x\gordon.yaml", docker_exe="docker")
    with _pytest.raises(RuntimeError, match="плагин"):
        adapter.init()

    monkeypatch.setattr(da, "_has_agent_plugin", lambda exe: True)
    adapter = DockerAgentLLM(agent_file=r"C:\x\gordon.yaml", docker_exe="docker")
    with _pytest.raises(RuntimeError, match="конфиг"):
        adapter.init()


def test_docker_agent_in_config_and_pool_build():
    from lab100.cli import build, load_config

    cfg = load_config("config/agents.toml")
    assert "docker-agent" in cfg["provider"]
    assert cfg["provider"]["docker-agent"]["kind"] == "llm"
    assert cfg["quota"]["docker-agent"]["window"] == "forever"
    assert "docker-cloud" in cfg["provider"]
    assert cfg["provider"]["docker-cloud"]["model"] == "groq/llama-3.3-70b-versatile"
    assert cfg["quota"]["docker-cloud"]["window"] == "forever"

    orch = build("config/agents.toml")
    # адаптер присутствует в пуле ИЛИ корректно исключён (плагин docker agent может быть не установлен):
    names = {a.name for a in orch.registry.by_kind("llm")}
    notes = getattr(orch, "_notes", [])
    assert "docker-agent" in names or any("docker-agent" in n for n in notes)
    assert "docker-cloud" in names or any("docker-cloud" in n for n in notes)