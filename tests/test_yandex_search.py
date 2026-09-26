"""Тесты адаптера Yandex Search API (сеть не используется).

Сеть замокана на уровне urllib.request.urlopen: проверяем форму запроса,
разбор base64/XML-выдачи, валидацию запроса и маппинг ошибок.
"""

import base64
import json
import sys
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lab100.providers import yandex_search as ys  # noqa: E402
from lab100.providers.base import AdapterError  # noqa: E402
from lab100.providers.yandex_search import YandexSearch  # noqa: E402

XML_FIXTURE = """<?xml version="1.0" encoding="utf-8"?>
<yandexsearch version="1.0">
  <request>
    <query>тестовый запрос</query>
  </request>
  <response date="2026-01-01">
    <found priority="phrase">2</found>
    <results>
      <grouping attr="d" mode="flat" groups-on-page="5" docs-in-group="1">
        <page first="1" last="1">0</page>
        <group>
          <doccount>1</doccount>
          <doc id="1">
            <url>https://example.com/a</url>
            <domain>example.com</domain>
            <title>Первый документ</title>
            <headline>Сниппет первого документа</headline>
            <modtime>20260101T120000Z</modtime>
            <size>1024</size>
            <charset>utf-8</charset>
            <passages>
              <passage>Фрагмент один</passage>
              <passage>Фрагмент два</passage>
            </passages>
          </doc>
        </group>
        <group>
          <doccount>1</doccount>
          <doc id="2">
            <url>https://example.org/b</url>
            <domain>example.org</domain>
            <title>Второй документ</title>
            <passages>
              <passage>Единственный фрагмент</passage>
            </passages>
          </doc>
        </group>
        <group>
          <doccount>1</doccount>
          <doc id="3">
            <url>https://example.com/a</url>
            <domain>example.com</domain>
            <title>Дубликат URL</title>
          </doc>
        </group>
      </grouping>
    </results>
  </response>
</yandexsearch>
"""


class FakeResponse:
    def __init__(self, payload: dict):
        self._body = json.dumps(payload).encode("utf-8")
        self.status = 200

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def raw_response(xml_text: str = XML_FIXTURE) -> dict:
    return {"rawData": base64.b64encode(xml_text.encode("utf-8")).decode("ascii")}


def make_adapter(monkeypatch, xml_text: str = XML_FIXTURE):
    """Адаптер с подменённым urlopen; возвращает (adapter, captured)."""
    captured: dict = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["headers"] = dict(req.headers)
        captured["body"] = json.loads(req.data.decode("utf-8"))
        captured["method"] = req.get_method()
        return FakeResponse(raw_response(xml_text))

    monkeypatch.setattr(ys.urllib.request, "urlopen", fake_urlopen)
    adapter = YandexSearch(api_key="AQVN-test-key", folder_id="b1gtestfolder")
    adapter.init()
    return adapter, captured


# ── init / авторизация ───────────────────────────────────────────────────────
def test_init_requires_credentials(monkeypatch):
    monkeypatch.delenv("YANDEX_SEARCH_API_KEY", raising=False)
    monkeypatch.delenv("YANDEX_SEARCH_IAM_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="YANDEX_SEARCH_API_KEY"):
        YandexSearch().init()


def test_init_reads_env_folder_id(monkeypatch):
    monkeypatch.setenv("YANDEX_SEARCH_API_KEY", "AQVN-env")
    monkeypatch.setenv("YANDEX_SEARCH_FOLDER_ID", "envfolder")
    adapter = YandexSearch()
    adapter.init()
    assert adapter.folder_id == "envfolder"
    assert adapter._auth_header() == "Api-Key AQVN-env"


def test_iam_token_uses_bearer(monkeypatch):
    monkeypatch.delenv("YANDEX_SEARCH_API_KEY", raising=False)
    monkeypatch.setenv("YANDEX_SEARCH_IAM_TOKEN", "t1.iam-token")
    adapter = YandexSearch()
    adapter.init()
    assert adapter._auth_header() == "Bearer t1.iam-token"


# ── форма запроса ────────────────────────────────────────────────────────────
def test_search_request_shape(monkeypatch):
    adapter, cap = make_adapter(monkeypatch)
    adapter.search("пушистый единорог", limit=5)

    assert cap["method"] == "POST"
    assert cap["url"] == ys.API_URL
    body = cap["body"]
    assert body["query"]["queryText"] == "пушистый единорог"
    assert body["query"]["searchType"] == "SEARCH_TYPE_RU"
    assert body["query"]["page"] == "0"
    assert body["groupSpec"]["groupMode"] == "GROUP_MODE_FLAT"
    assert body["groupSpec"]["groupsOnPage"] == "5"
    assert body["groupSpec"]["docsInGroup"] == "1"
    assert body["responseFormat"] == "FORMAT_XML"
    assert body["folderId"] == "b1gtestfolder"


def test_search_caps_groups_on_page(monkeypatch):
    adapter, cap = make_adapter(monkeypatch)
    adapter.search("запрос", limit=500)
    assert cap["body"]["groupSpec"]["groupsOnPage"] == str(ys.MAX_GROUPS_ON_PAGE)


def test_request_header_has_no_secret_in_url(monkeypatch):
    adapter, cap = make_adapter(monkeypatch)
    adapter.search("запрос", limit=3)
    assert cap["headers"]["Authorization"] == "Api-Key AQVN-test-key"
    assert "AQVN-test-key" not in cap["url"]


# ── разбор выдачи ────────────────────────────────────────────────────────────
def test_search_parses_results(monkeypatch):
    adapter, _ = make_adapter(monkeypatch)
    results, credits = adapter.search("запрос", limit=5)

    assert credits == 1
    assert [r.url for r in results] == [
        "https://example.com/a",
        "https://example.org/b",
    ]
    first = results[0]
    assert first.title == "Первый документ"
    assert first.description == "Сниппет первого документа"
    assert first.position == 1
    assert first.extra["domain"] == "example.com"
    assert first.extra["passages"] == ["Фрагмент один", "Фрагмент два"]


def test_search_falls_back_to_passage_when_no_headline(monkeypatch):
    adapter, _ = make_adapter(monkeypatch)
    results, _ = adapter.search("запрос", limit=5)
    assert results[1].description == "Единственный фрагмент"


def test_search_respects_limit(monkeypatch):
    adapter, _ = make_adapter(monkeypatch)
    results, _ = adapter.search("запрос", limit=1)
    assert len(results) == 1


def test_search_dedupes_urls(monkeypatch):
    adapter, _ = make_adapter(monkeypatch)
    results, _ = adapter.search("запрос", limit=10)
    assert len({r.url for r in results}) == len(results)


def test_empty_results_raises(monkeypatch):
    empty = '<?xml version="1.0"?><yandexsearch><response><found>0</found><results/></response></yandexsearch>'
    adapter, _ = make_adapter(monkeypatch, xml_text=empty)
    with pytest.raises(AdapterError, match="пуста"):
        adapter.search("ничего не найдётся", limit=5)


# ── ошибки ───────────────────────────────────────────────────────────────────
def test_http_401_maps_to_adapter_error(monkeypatch):
    def boom(req, timeout=None):
        raise urllib.error.HTTPError(
            ys.API_URL, 401, "Unauthorized", {}, None
        )

    monkeypatch.setattr(ys.urllib.request, "urlopen", boom)
    adapter = YandexSearch(api_key="AQVN-secret-value")
    adapter.init()
    with pytest.raises(AdapterError) as exc:
        adapter.search("запрос")
    assert "401" in str(exc.value)
    assert "AQVN-secret-value" not in str(exc.value)


def test_http_429_maps_to_adapter_error(monkeypatch):
    def boom(req, timeout=None):
        raise urllib.error.HTTPError(ys.API_URL, 429, "Too Many Requests", {}, None)

    monkeypatch.setattr(ys.urllib.request, "urlopen", boom)
    adapter = YandexSearch(api_key="AQVN-key")
    adapter.init()
    with pytest.raises(AdapterError, match="429"):
        adapter.search("запрос")


def test_network_error_maps_to_adapter_error(monkeypatch):
    def boom(req, timeout=None):
        raise urllib.error.URLError("connection reset")

    monkeypatch.setattr(ys.urllib.request, "urlopen", boom)
    adapter = YandexSearch(api_key="AQVN-key")
    adapter.init()
    with pytest.raises(AdapterError, match="network"):
        adapter.search("запрос")


def test_malformed_xml_raises(monkeypatch):
    adapter, _ = make_adapter(monkeypatch, xml_text="<yandexsearch><broken>")
    with pytest.raises(AdapterError, match="XML"):
        adapter.search("запрос")


def test_response_without_rawdata_raises(monkeypatch):
    monkeypatch.setattr(
        ys.urllib.request,
        "urlopen",
        lambda req, timeout=None: FakeResponse({"nothing": 1}),
    )
    adapter = YandexSearch(api_key="AQVN-key")
    adapter.init()
    with pytest.raises(AdapterError, match="rawData"):
        adapter.search("запрос")


def test_non_object_response_raises(monkeypatch):
    monkeypatch.setattr(
        ys.urllib.request,
        "urlopen",
        lambda req, timeout=None: FakeResponse([1, 2, 3]),
    )
    adapter = YandexSearch(api_key="AQVN-key")
    adapter.init()
    with pytest.raises(AdapterError, match="JSON-объект"):
        adapter.search("запрос")


# ── клиентская валидация ─────────────────────────────────────────────────────
def test_empty_query_rejected(monkeypatch):
    adapter, _ = make_adapter(monkeypatch)
    with pytest.raises(AdapterError, match="пустой"):
        adapter.search("   ")


def test_too_long_query_rejected(monkeypatch):
    adapter, _ = make_adapter(monkeypatch)
    with pytest.raises(AdapterError, match="400"):
        adapter.search("я" * (ys.MAX_QUERY_CHARS + 1))


def test_too_many_words_rejected(monkeypatch):
    adapter, _ = make_adapter(monkeypatch)
    with pytest.raises(AdapterError, match="40"):
        adapter.search(" ".join(["слово"] * (ys.MAX_QUERY_WORDS + 1)))


# ── скрейпинг ────────────────────────────────────────────────────────────────
def test_scrape_not_supported(monkeypatch):
    adapter, _ = make_adapter(monkeypatch)
    with pytest.raises(AdapterError, match="скрейпинг"):
        adapter.scrape("https://example.com/a")


# ── интеграция с пулом ───────────────────────────────────────────────────────
def test_factory_builds_yandex_adapter():
    from lab100.providers import build_search_adapter

    adapter = build_search_adapter(
        "yandex-search",
        {"api_key": "AQVN-key", "folder_id": "f1", "max_passages": 4},
    )
    assert isinstance(adapter, YandexSearch)
    assert adapter.folder_id == "f1"
    assert adapter.max_passages == 4


def test_config_has_yandex_provider_and_quota():
    from lab100.cli import load_config

    cfg = load_config("config/agents.toml")
    assert cfg["provider"]["yandex-search"]["kind"] == "search"
    assert cfg["provider"]["yandex-search"]["capabilities"] == ["web-search"]
    assert cfg["quota"]["yandex-search"]["window"] == "month"
    assert cfg["quota"]["yandex-search"]["limit"] == 500


def test_yandex_excluded_without_key(monkeypatch):
    from lab100.cli import build

    monkeypatch.delenv("YANDEX_SEARCH_API_KEY", raising=False)
    monkeypatch.delenv("YANDEX_SEARCH_IAM_TOKEN", raising=False)
    orch = build("config/agents.toml")
    names = {a.name for a in orch.registry.by_kind("search")}
    notes = getattr(orch, "_notes", [])
    assert "yandex-search" not in names
    assert any("yandex-search" in n for n in notes)


def test_yandex_in_pool_with_key(monkeypatch):
    from lab100.cli import build

    monkeypatch.setenv("YANDEX_SEARCH_API_KEY", "AQVN-key")
    orch = build("config/agents.toml")
    agent = orch.registry.get("yandex-search")
    assert agent is not None
    assert agent.has_capability("web-search")
    assert not agent.has_capability("scrape")
    assert agent.priority < 20  # ниже бесплатных локальных
