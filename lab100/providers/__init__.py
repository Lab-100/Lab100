"""Провайдеры Lab100."""

from .base import AdapterError, SearchAdapter, LLMAdapter, SearchResult
from .firecrawl import FirecrawlSearch
from .ollama import OllamaLLM
from .gemini import GeminiLLM


def build_search_adapter(name: str, config: dict) -> SearchAdapter:
    if name == "firecrawl":
        return FirecrawlSearch(api_key=config.get("api_key"))
    raise RuntimeError(f"нет search-адаптера: {name}")


def build_llm_adapter(name: str, config: dict) -> LLMAdapter:
    if name == "ollama":
        return OllamaLLM(base_url=config.get("base_url"), default_model=config.get("model"))
    if name == "gemini":
        return GeminiLLM(api_key=config.get("api_key"), default_model=config.get("model"))
    raise RuntimeError(f"нет llm-адаптера: {name}")


__all__ = [
    "AdapterError",
    "SearchAdapter",
    "LLMAdapter",
    "SearchResult",
    "FirecrawlSearch",
    "OllamaLLM",
    "GeminiLLM",
    "build_search_adapter",
    "build_llm_adapter",
]