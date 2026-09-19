"""Провайдер local-browser: локальный веб-агент через браузер-драйвер.

Использует headless Edge/Chrome (patchright) через HTTP-драйвер
(по умолчанию 127.0.0.1:8123, driver.mjs). Поиск идёт через поисковый движок
без API-ключей (Bing/DuckDuckGo), скрейпинг — реальным браузером (JS-стейты).
Это Кейс 1: полностью локально, без Firecrawl, расход = 0 кредитов.

Драйвер автоматически поднимается, если доступен node.exe (Windows).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request

from .base import AdapterError, SearchAdapter, SearchResult

DRIVER_DIR = r"%LOCALAPPDATA%\Temp\opencode\browsertool"
DRIVER_URL = "http://127.0.0.1:8123"


def _default_node() -> str:
    for cand in (r"C:\Program Files\nodejs\node.exe", "node"):
        if cand and os.path.exists(cand) if cand and os.path.sep in cand else True:
            if os.path.sep in cand and not os.path.exists(cand):
                continue
            return cand
    return "node"


class LocalBrowserSearch(SearchAdapter):
    name = "local-browser"

    def __init__(
        self,
        base_url: str | None = None,
        engine: str = "bing",
        auto_start: bool = True,
        node: str | None = None,
    ) -> None:
        self._base = base_url or DRIVER_URL
        self.engine = engine
        self.auto_start = auto_start
        self._node = node
        self._proc: subprocess.Popen | None = None

    # ── жизненный цикл ───────────────────────────────────────────────────────
    def init(self) -> None:
        if not self._ping():
            if not (self.auto_start and self._spawn_driver()):
                raise RuntimeError(
                    f"local-browser: драйвер не доступен на {self._base} "
                    "(нужен запущенный driver.mjs или node.exe)"
                )

    def _ping(self) -> bool:
        try:
            with urllib.request.urlopen(self._base, timeout=2) as resp:
                return resp.status == 200
        except Exception:
            return False

    def _spawn_driver(self) -> bool:
        driver_path = os.path.join(DRIVER_DIR, "driver.mjs")
        if not os.path.exists(driver_path):
            return False
        node = self._node or _default_node()
        try:
            flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            self._proc = subprocess.Popen(
                [node, driver_path],
                cwd=DRIVER_DIR,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=flags,
            )
        except Exception:
            return False
        for _ in range(30):  # до 30 сек
            if self._ping():
                return True
            if self._proc.poll() is not None:
                return False
            time.sleep(1)
        return self._ping()

    # ── запросы к драйверу ───────────────────────────────────────────────────
    def _cmd(self, payload: dict, timeout: int = 90) -> dict:
        req = urllib.request.Request(
            self._base,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            raise AdapterError(f"local-browser: {exc}") from exc

    # ── поиск ────────────────────────────────────────────────────────────────
    def search(self, query: str, limit: int = 5) -> tuple[list[SearchResult], int]:
        if self.engine == "duckduckgo":
            return self._search_duckduckgo(query, limit)
        if self.engine == "wiby":
            return self._search_wiby(query, limit)
        if self.engine == "wikipedia":
            return self._search_wikipedia(query, limit)
        return self._search_bing(query, limit)

    def _search_bing(self, query: str, limit: int = 5) -> tuple[list[SearchResult], int]:
        url = f"https://www.bing.com/search?q={urllib.parse.quote(query)}"
        self._cmd({"cmd": "nav", "url": url, "wait": 3000})
        js = """(() => {
          const out = [];
          for (const li of document.querySelectorAll('#b_results > li.b_algo')) {
            const a = li.querySelector('h2 a');
            if (!a) continue;
            const cap = li.querySelector('.b_attribution cite')?.innerText
                     || li.querySelector('.b_caption p')?.innerText || '';
            out.push({ url: a.href, title: (a.innerText || '').trim(),
                       description: cap.replace(/\\s+/g, ' ').slice(0, 400) });
          }
          return out;
        })()"""
        data = self._cmd({"cmd": "eval", "js": js})
        items = data.get("res", []) if isinstance(data, dict) else []
        results = [
            SearchResult(
                url=str(it.get("url", "")),
                title=str(it.get("title", "")),
                description=str(it.get("description", "")),
                position=i,
            )
            for i, it in enumerate(items[:limit], 1)
        ]
        if not results:
            raise AdapterError("local-browser: поиск не дал результатов (капча/сеть?)")
        return results, 0

    def _search_wiby(self, query: str, limit: int = 5) -> tuple[list[SearchResult], int]:
        """Wiby.me — маленький поисковик, не блокирует ботов, отдаёт внешние ссылки."""
        url = f"https://wiby.me/?q={urllib.parse.quote(query)}"
        self._cmd({"cmd": "nav", "url": url, "wait": 2500})
        js = """(() => {
          const out = [];
          for (const a of document.querySelectorAll('main a, .results a, body a')) {
            const t = (a.innerText || '').trim();
            if (!t || t.length < 5) continue;
            const u = a.href;
            if (!/^https?:/.test(u)) continue;
            if (/wiby\\.me|settings/.test(u)) continue;
            if (out.some(x => x.url === u)) continue;
            out.push({ url: u, title: t.slice(0, 120) });
            if (out.length >= 8) break;
          }
          return out;
        })()"""
        data = self._cmd({"cmd": "eval", "js": js})
        items = data.get("res", []) if isinstance(data, dict) else []
        results = [
            SearchResult(url=str(it.get("url", "")), title=str(it.get("title", "")), position=i)
            for i, it in enumerate(items[:limit], 1)
        ]
        if not results:
            raise AdapterError("local-browser: Wiby не дал результатов")
        return results, 0

    def _search_wikipedia(self, query: str, limit: int = 5) -> tuple[list[SearchResult], int]:
        """Wikipedia search — стабильный движок без капч и ключей."""
        url = f"https://en.wikipedia.org/w/index.php?search={urllib.parse.quote(query)}"
        self._cmd({"cmd": "nav", "url": url, "wait": 2500})
        js = """(() => {
          const out = [];
          for (const li of document.querySelectorAll('.mw-search-result-heading')) {
            const a = li.querySelector('a'); if (!a) continue;
            out.push({ url: a.href, title: (a.innerText || '').trim() });
          }
          return out;
        })()"""
        data = self._cmd({"cmd": "eval", "js": js})
        items = data.get("res", []) if isinstance(data, dict) else []
        results = [
            SearchResult(
                url=f"https://en.wikipedia.org{str(it.get('url', ''))}",
                title=str(it.get("title", "")),
                position=i,
            )
            for i, it in enumerate(items[:limit], 1)
        ]
        if not results:
            raise AdapterError("local-browser: Wikipedia не дала результатов")
        return results, 0

    def _search_duckduckgo(self, query: str, limit: int = 5) -> tuple[list[SearchResult], int]:
        url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(query)}"
        self._cmd({"cmd": "nav", "url": url, "wait": 3000})
        js = """(() => {
          const out = [];
          for (const r of document.querySelectorAll('.result')) {
            const a = r.querySelector('.result__a');
            if (!a) continue;
            const s = r.querySelector('.result__snippet')?.innerText || '';
            out.push({ url: a.href, title: (a.innerText || '').trim(),
                       description: s.replace(/\\s+/g, ' ').slice(0, 400) });
          }
          return out;
        })()"""
        data = self._cmd({"cmd": "eval", "js": js})
        items = data.get("res", []) if isinstance(data, dict) else []
        results = [
            SearchResult(
                url=str(it.get("url", "")),
                title=str(it.get("title", "")),
                description=str(it.get("description", "")),
                position=i,
            )
            for i, it in enumerate(items[:limit], 1)
        ]
        if not results:
            raise AdapterError("local-browser: DDG не дал результатов (капча/сеть?)")
        return results, 0

    # ── скрейпинг ────────────────────────────────────────────────────────────
    def scrape(self, url: str) -> tuple[str, int]:
        data = self._cmd({"cmd": "get", "url": url, "timeout": 60000})
        text = (data.get("text") or "").strip()
        if not text:
            raise AdapterError(f"local-browser: пустая страница {url}")
        return text, 0