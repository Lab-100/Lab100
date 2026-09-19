"""Провайдер Ollama: локальный LLM через REST API.

Endpoint http://localhost:11434/api/chat. Работает офлайн, бесплатно.
Модели и список: список через /api/tags. Расход считается приблизительно
по "оценочным токенам" (символы/4 для отображения; квота = unlimited).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from .base import AdapterError, LLMAdapter

OLLAMA_BASE = "http://127.0.0.1:11434"


class OllamaLLM(LLMAdapter):
    name = "ollama"

    def __init__(self, base_url: str | None = None, default_model: str = "hermes3:3b") -> None:
        self._base = base_url or OLLAMA_BASE
        self.default_model = default_model
        self.models: list[str] = []

    def init(self) -> None:
        try:
            req = urllib.request.Request(f"{self._base}/api/tags", method="GET")
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            self.models = [m.get("name", "") for m in data.get("models", [])]
        except Exception as exc:
            raise RuntimeError(f"ollama: сервер недоступен ({exc})") from exc
        if not self.models:
            raise RuntimeError("ollama: нет ни одной модели")

    def _available_model(self, model: str | None) -> str:
        want = model or self.default_model
        if want in self.models:
            return want
        # ближайший по префиксу, иначе первая модель сервера
        for m in self.models:
            if m.startswith(want.split(":")[0]):
                return m
        return self.models[0]

    def chat(
        self, prompt: str, system: str = "", max_tokens: int = 1200, model: str | None = None
    ) -> tuple[str, dict]:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": self._available_model(model),
            "messages": messages,
            "stream": False,
            "options": {"num_predict": max_tokens},
        }
        req = urllib.request.Request(
            f"{self._base}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=600) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise AdapterError(f"ollama HTTP {exc.code}: {exc.read()[:200]}") from exc
        except urllib.error.URLError as exc:
            raise AdapterError(f"ollama network: {exc.reason}") from exc

        content = (data.get("message", {}) or {}).get("content", "")
        metrics = {
            "model": payload["model"],
            "prompt_tokens": data.get("prompt_eval_count", 0),
            "completion_tokens": data.get("eval_count", 0),
            "tokens": data.get("prompt_eval_count", 0) + data.get("eval_count", 0),
            "credits": 0,
        }
        return content.strip(), metrics