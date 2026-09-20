"""Провайдер Exa: семантический веб-поиск и извлечение контента.

REST API (без SDK): https://api.exa.ai/search и /contents.
Рекомендуемый запрос по руководству Exa: query + contents.highlights.
Ключ — из переменной окружения EXA_API_KEY.
Расход может учитываться числом запросов (у пула — 1 за вызов).
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from .base import AdapterError, SearchAdapter, SearchResult

API_BASE = "https://api.exa.ai"

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)


class ExaSearch(SearchAdapter):
    name = "exa"

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key

    def init(self) -> None:
        if self._api_key is None:
            self._api_key = os.environ.get("EXA_API_KEY")
        if not self._api_key:
            raise RuntimeError("exa: отсутствует EXA_API_KEY")

    def _post(self, path: str, body: dict, timeout: int = 60) -> dict:
        req = urllib.request.Request(
            f"{API_BASE}/{path}",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "x-api-key": self._api_key,
                "Content-Type": "application/json",
                "User-Agent": _USER_AGENT,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
            raise AdapterError(f"exa HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise AdapterError(f"exa network: {exc.reason}") from exc

    def search(self, query: str, limit: int = 5) -> tuple[list[SearchResult], int]:
        data = self._post(
            "search",
            {"query": query, "contents": {"highlights": True}, "numResults": limit},
        )
        items = data.get("results", []) or []
        results = [
            SearchResult(
                url=str(item.get("url", "")),
                title=str(item.get("title", "")),
                description="\n\n".join(
                    h for h in (item.get("highlights") or []) if h
                ),
                position=i,
                extra={
                    "authors": item.get("author") or [],
                    "publishedDate": item.get("publishedDate") or "",
                },
            )
            for i, item in enumerate(items)
        ]
        return results, 1

    def scrape(self, url: str) -> tuple[str, int]:
        data = self._post("contents", {"urls": [url], "text": True})
        items = data.get("results", []) or []
        if not items:
            raise AdapterError(f"exa: нет контента для {url}")
        text = items[0].get("text") or ""
        if not text.strip():
            raise AdapterError(f"exa: пустой контент для {url} (доступ запрещён?)")
        return text, 1