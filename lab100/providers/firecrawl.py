"""Провайдер Firecrawl: веб-поиск и скрейпинг.

REST API v2 (без SDK): https://api.firecrawl.dev/v2/search и /v2/scrape.
Ключ — из переменной окружения FIRECRAWL_API_KEY.
Расход кредитов возвращается в creditsUsed.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from .base import AdapterError, SearchAdapter, SearchResult

API_BASE = "https://api.firecrawl.dev/v2"


class FirecrawlSearch(SearchAdapter):
    name = "firecrawl"

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key

    def init(self) -> None:
        if not self._api_key:
            self._api_key = os.environ.get("FIRECRAWL_API_KEY")
        if not self._api_key:
            raise RuntimeError("firecrawl: отсутствует FIRECRAWL_API_KEY")

    def _call(self, path: str, body: dict) -> dict:
        req = urllib.request.Request(
            f"{API_BASE}/{path}",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
            raise AdapterError(f"firecrawl HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise AdapterError(f"firecrawl network: {exc.reason}") from exc

    def search(self, query: str, limit: int = 5) -> tuple[list[SearchResult], int]:
        data = self._call("search", {"query": query, "limit": limit})
        web = data.get("data", {}).get("web", []) if isinstance(data.get("data"), dict) else []
        results = [
            SearchResult(
                url=str(item.get("url", "")),
                title=str(item.get("title", "")),
                description=str(item.get("description", "")),
                position=int(item.get("position", i)),
            )
            for i, item in enumerate(web)
        ]
        credits = int(data.get("creditsUsed", 1))
        return results, credits

    def scrape(self, url: str) -> tuple[str, int]:
        data = self._call("scrape", {"url": url})
        markdown = data.get("data", {}).get("markdown", "") or ""
        credits = int(data.get("creditsUsed", 1))
        return markdown, credits