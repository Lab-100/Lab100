"""Провайдер Gemini (Google AI Studio key).

Подключается, только если в окружении есть GEMINI_API_KEY.
Endpoint: generativelanguage.googleapis.com/v1beta (универсальный, нелокальный).
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request

from .base import AdapterError, LLMAdapter

GEMINI_API = "https://generativelanguage.googleapis.com/v1beta/models"


class GeminiLLM(LLMAdapter):
    name = "gemini"

    def __init__(self, api_key: str | None = None, default_model: str = "gemini-2.5-flash") -> None:
        self._api_key = api_key
        self.default_model = default_model

    def init(self) -> None:
        if not self._api_key:
            self._api_key = os.environ.get("GEMINI_API_KEY")
        if not self._api_key:
            raise RuntimeError("gemini: отсутствует GEMINI_API_KEY")

    def chat(
        self, prompt: str, system: str = "", max_tokens: int = 1200, model: str | None = None
    ) -> tuple[str, dict]:
        model_name = model or self.default_model
        url = f"{GEMINI_API}/{model_name}:generateContent?key={urllib.parse.quote(self._api_key)}"
        contents = []
        if system:
            contents.append({"role": "user", "parts": [{"text": f"(Система) {system}\n\n{prompt}"}]})
        else:
            contents.append({"role": "user", "parts": [{"text": prompt}]})
        payload = {"contents": contents, "generationConfig": {"maxOutputTokens": max_tokens}}

        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
            raise AdapterError(f"gemini HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise AdapterError(f"gemini network: {exc.reason}") from exc

        content = ""
        try:
            content = data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, TypeError):
            content = json.dumps(data, ensure_ascii=False)[:300]
        usage = data.get("usageMetadata", {})
        metrics = {
            "model": model_name,
            "prompt_tokens": usage.get("promptTokenCount", 0),
            "completion_tokens": usage.get("candidatesTokenCount", 0),
            "tokens": usage.get("totalTokenCount", 0),
            "credits": 0,
        }
        return content.strip(), metrics