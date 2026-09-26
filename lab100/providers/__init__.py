"""Провайдеры Lab100."""

from .base import AdapterError, SearchAdapter, LLMAdapter, SearchResult
from .docker_agent import DockerAgentLLM
from .exa import ExaSearch
from .firecrawl import FirecrawlSearch
from .local_browser import LocalBrowserSearch
from .ollama import OllamaLLM
from .gemini import GeminiLLM
from .plain_http import PlainHttpScrape
from .yandex_search import YandexSearch


def build_search_adapter(name: str, config: dict) -> SearchAdapter:
    if name.startswith("exa"):
        return ExaSearch(api_key=config.get("api_key"))
    if name.startswith("firecrawl"):
        return FirecrawlSearch(api_key=config.get("api_key"))
    if name.startswith("yandex"):
        return YandexSearch(
            api_key=config.get("api_key"),
            iam_token=config.get("iam_token"),
            folder_id=config.get("folder_id"),
            search_type=config.get("search_type", "SEARCH_TYPE_RU"),
            family_mode=config.get("family_mode", "FAMILY_MODE_MODERATE"),
            max_passages=int(config.get("max_passages", 2)),
        )
    if name.startswith("local-browser"):
        return LocalBrowserSearch(
            base_url=config.get("base_url"),
            engine=config.get("engine", "bing"),
            auto_start=bool(config.get("auto_start", True)),
        )
    if name.startswith("plain-http"):
        return PlainHttpScrape()
    raise RuntimeError(f"нет search-адаптера: {name}")


def build_llm_adapter(name: str, config: dict) -> LLMAdapter:
    if name.startswith("ollama"):
        return OllamaLLM(base_url=config.get("base_url"), default_model=config.get("model"))
    if name.startswith("gemini"):
        return GeminiLLM(api_key=config.get("api_key"), default_model=config.get("model"))
    if name.startswith("docker-"):
        return DockerAgentLLM(
            agent_file=config.get("agent_file"),
            model=config.get("model", "local"),
            docker_exe=config.get("docker_exe"),
            timeout_s=int(config.get("timeout_s", 600)),
        )
    raise RuntimeError(f"нет llm-адаптера: {name}")


__all__ = [
    "AdapterError",
    "SearchAdapter",
    "LLMAdapter",
    "SearchResult",
    "ExaSearch",
    "FirecrawlSearch",
    "LocalBrowserSearch",
    "PlainHttpScrape",
    "YandexSearch",
    "OllamaLLM",
    "GeminiLLM",
    "DockerAgentLLM",
    "build_search_adapter",
    "build_llm_adapter",
]