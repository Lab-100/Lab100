"""Провайдер Yandex Search API: веб-поиск по базе Яндекса.

REST API v2 (без SDK): POST https://searchapi.api.cloud.yandex.net/v2/web/search
Тело: {"query": {...}, "groupSpec": {...}, "folderId": ..., "responseFormat": "FORMAT_XML"}
Ответ 200: {"rawData": base64(XML)}, XML парсится через xml.etree.

Аутентификация (любой из двух способов, см. api-ref/authentication):
- API-ключ кабинета   → заголовок `Authorization: Api-Key <KEY>`, env YANDEX_SEARCH_API_KEY;
- сервисный аккаунт   → заголовок `Authorization: Bearer <IAM-токен>`, env YANDEX_SEARCH_IAM_TOKEN.

folderId (env YANDEX_SEARCH_FOLDER_ID или секция конфига) нужен для сервисного
аккаунта; для API-ключа можно не указывать.

Сервис платный: тарификация по числу запросов, поэтому провайдер ставится в конец
каскада с месячной квотой. Лимиты сервиса: запрос ≤ 400 символов и ≤ 40 слов,
не более 100 групп на страницу, до 3 документов в группе.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

from .base import AdapterError, SearchAdapter, SearchResult

API_URL = "https://searchapi.api.cloud.yandex.net/v2/web/search"

MAX_QUERY_CHARS = 400
MAX_QUERY_WORDS = 40
MAX_GROUPS_ON_PAGE = 100


def _text(node: ET.Element | None) -> str:
    """Текст узла без вложенной разметки (None-безопасно)."""
    if node is None or node.text is None:
        return ""
    return "".join(node.itertext()).strip()


class YandexSearch(SearchAdapter):
    name = "yandex-search"

    def __init__(
        self,
        api_key: str | None = None,
        iam_token: str | None = None,
        folder_id: str | None = None,
        search_type: str = "SEARCH_TYPE_RU",
        family_mode: str = "FAMILY_MODE_MODERATE",
        max_passages: int = 2,
        timeout: int = 60,
    ) -> None:
        self._api_key = api_key
        self._iam_token = iam_token
        self.folder_id = folder_id
        self.search_type = search_type
        self.family_mode = family_mode
        self.max_passages = max(1, min(int(max_passages), 5))
        self.timeout = timeout

    # ── жизненный цикл ───────────────────────────────────────────────────────
    def init(self) -> None:
        if not self._api_key:
            self._api_key = os.environ.get("YANDEX_SEARCH_API_KEY") or None
        if not self._iam_token:
            self._iam_token = os.environ.get("YANDEX_SEARCH_IAM_TOKEN") or None
        if not (self._api_key or self._iam_token):
            raise RuntimeError(
                "yandex-search: нужен YANDEX_SEARCH_API_KEY (или YANDEX_SEARCH_IAM_TOKEN)"
            )
        if not self.folder_id:
            self.folder_id = os.environ.get("YANDEX_SEARCH_FOLDER_ID") or None

    def _auth_header(self) -> str:
        if self._api_key:
            return f"Api-Key {self._api_key}"
        return f"Bearer {self._iam_token}"

    # ── HTTP ─────────────────────────────────────────────────────────────────
    def _post(self, body: dict) -> str:
        payload = dict(body)
        if self.folder_id and not payload.get("folderId"):
            payload["folderId"] = self.folder_id
        req = urllib.request.Request(
            API_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": self._auth_header(),
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
            hint = {
                400: "неверный запрос (проверьте длину запроса и параметры)",
                401: "ключ/токен не прошёл аутентификацию",
                403: "нет прав на поиск (нужна роль search-api.user или executor)",
                429: "исчерпана квота запросов",
            }.get(exc.code, "")
            raise AdapterError(
                f"yandex-search HTTP {exc.code}{': ' + hint if hint else ''} — {detail}"
            ) from exc
        except urllib.error.URLError as exc:
            raise AdapterError(f"yandex-search network: {exc.reason}") from exc
        except (ValueError, KeyError) as exc:
            raise AdapterError(f"yandex-search: некорректный ответ API ({exc})") from exc
        return self._decode_raw(data)

    @staticmethod
    def _decode_raw(data: object) -> str:
        if not isinstance(data, dict):
            raise AdapterError(
                f"yandex-search: ожидался JSON-объект, получен {type(data).__name__}"
            )
        raw = data.get("rawData")
        if not raw:
            raise AdapterError("yandex-search: ответ без rawData")
        try:
            return base64.b64decode(raw).decode("utf-8", errors="replace")
        except (binascii.Error, ValueError) as exc:
            raise AdapterError(f"yandex-search: rawData не декодируется ({exc})") from exc

    # ── разбор XML-выдачи ────────────────────────────────────────────────────
    def _parse(self, xml_text: str, limit: int) -> list[SearchResult]:
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as exc:
            raise AdapterError(f"yandex-search: невалидный XML выдачи ({exc})") from exc

        results: list[SearchResult] = []
        seen: set[str] = set()
        for doc in root.iter("doc"):
            url = _text(doc.find("url"))
            if not url or url in seen:
                continue
            seen.add(url)
            passages = [_text(p) for p in doc.findall("passages/passage")]
            headline = _text(doc.find("headline"))
            description = headline or "\n\n".join(p for p in passages if p)
            results.append(
                SearchResult(
                    url=url,
                    title=_text(doc.find("title")),
                    description=description,
                    position=len(results) + 1,
                    extra={
                        "domain": _text(doc.find("domain")),
                        "modtime": _text(doc.find("modtime")),
                        "charset": _text(doc.find("charset")),
                        "passages": [p for p in passages if p],
                    },
                )
            )
            if len(results) >= limit:
                break
        return results

    # ── поиск ────────────────────────────────────────────────────────────────
    def search(self, query: str, limit: int = 5) -> tuple[list[SearchResult], int]:
        text = (query or "").strip()
        if not text:
            raise AdapterError("yandex-search: пустой запрос")
        if len(text) > MAX_QUERY_CHARS:
            raise AdapterError(
                f"yandex-search: запрос длиннее {MAX_QUERY_CHARS} символов ({len(text)})"
            )
        if len(text.split()) > MAX_QUERY_WORDS:
            raise AdapterError(f"yandex-search: запрос длиннее {MAX_QUERY_WORDS} слов")

        groups = max(1, min(int(limit), MAX_GROUPS_ON_PAGE))
        body = {
            "query": {
                "searchType": self.search_type,
                "queryText": text,
                "familyMode": self.family_mode,
                "page": "0",
            },
            "groupSpec": {
                "groupMode": "GROUP_MODE_FLAT",
                "groupsOnPage": str(groups),
                "docsInGroup": "1",
            },
            "maxPassages": str(self.max_passages),
            "responseFormat": "FORMAT_XML",
        }
        results = self._parse(self._post(body), groups)
        if not results:
            raise AdapterError("yandex-search: выдача пуста")
        return results, 1  # тарификация — по запросам

    # ── скрейпинг не поддерживается ──────────────────────────────────────────
    def scrape(self, url: str) -> tuple[str, int]:
        raise AdapterError(
            "yandex-search: скрейпинг не поддерживается (только web-search)"
        )
