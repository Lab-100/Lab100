"""Провайдер plain-http: лёгкий скрейпинг без браузера и без ключей.

Простой HTTP GET через urllib (стандартная библиотека). Не рендерит JS,
поэтому — только статические страницы. Это самый быстрый и лёгкий вариант
Кейса 1 как запасной слой палитры.
"""

from __future__ import annotations

import base64
import html as html_mod
import re
import urllib.error
import urllib.parse
import urllib.request

from .base import AdapterError, SearchAdapter, SearchResult

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"


class PlainHttpScrape(SearchAdapter):
    name = "plain-http"

    @staticmethod
    def _clean_url(raw: str) -> str:
        """Декодировать реальный URL из redirect'а Bing (параметр u=, base64url).

        Bing оборачивает ссылки в bing.com/ck/a с base64url-параметром u=,
        который может иметь префикс-маркер (например 'a1') — перебираем оба.
        """
        raw = html_mod.unescape(raw).strip()
        if "bing.com/ck/a" not in raw:
            return raw
        q = urllib.parse.urlparse(raw).query
        enc = urllib.parse.parse_qs(q).get("u", [""])[0]
        if not enc:
            return raw
        for start in (0, 2):
            s = enc[start:]
            try:
                pad = "=" * (-len(s) % 4)
                dec = base64.urlsafe_b64decode(s + pad).decode("utf-8", "strict")
            except Exception:
                continue
            if dec.startswith(("http://", "https://")):
                return dec
        return raw

    def init(self) -> None:
        pass

    def search(self, query: str, limit: int = 5) -> tuple[list[SearchResult], int]:
        # Поиск через HTML-версию Bing без JS (не всегда стабильно), как запасной.
        url = "https://www.bing.com/search?q=" + urllib.parse.quote(query)  # type: ignore[attr-defined]
        try:
            req = urllib.request.Request(url, headers={"User-Agent": _UA})
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = resp.read().decode("utf-8", errors="replace")
        except Exception as exc:
            raise AdapterError(f"plain-http: поиск не удался ({exc})") from exc

        results: list[SearchResult] = []
        # Грубая выжимка: ссылки с содержимым <h2>.
        for m in re.finditer(r'<li class="b_algo".*?</li>', body, re.S):
            title = re.search(r"<h2[^>]*>.*?<a[^>]*>(.*?)</a>", m.group(0), re.S)
            href = re.search(r'<a[^>]+href="([^"]+)"', m.group(0))
            if not (title and href):
                continue
            t = html_mod.unescape(re.sub(r"<[^>]+>", "", title.group(1))).strip()
            results.append(SearchResult(url=self._clean_url(href.group(1)), title=t, position=len(results) + 1))
            if len(results) >= limit:
                break
        return results, 0

    def scrape(self, url: str) -> tuple[str, int]:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": _UA})
            with urllib.request.urlopen(req, timeout=30) as resp:
                ctype = resp.headers.get("Content-Type", "")
                if "text" not in ctype and "html" not in ctype and "xml" not in ctype:
                    return f"[бинарный/не-текстовый контент: {ctype}]", 0
                raw = resp.read()
                encoding = resp.headers.get_content_charset() or "utf-8"
                body = raw.decode(encoding, errors="replace")
        except urllib.error.HTTPError as exc:
            raise AdapterError(f"plain-http HTTP {exc.code} для {url}") from exc
        except urllib.error.URLError as exc:
            raise AdapterError(f"plain-http network: {exc.reason}") from exc

        text = self._html_to_text(body)
        if not text.strip():
            raise AdapterError(f"plain-http: пустая страница {url}")
        return text.strip(), 0

    @staticmethod
    def _html_to_text(body: str) -> str:
        # Убрать скрипты/стили, затем теги; схлопнуть пробелы.
        body = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", body)
        body = re.sub(r"(?is)<br\s*/?>", "\n", body)
        body = re.sub(r"(?is)</(p|div|h[1-6]|li|tr)>", "\n", body)
        body = re.sub(r"(?s)<[^>]+>", " ", body)
        body = html_mod.unescape(body)
        body = re.sub(r"[ \t]+", " ", body)
        body = re.sub(r"\n\s*\n+", "\n", body)
        return body