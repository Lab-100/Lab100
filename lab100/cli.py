"""CLI для пула агентов Lab100.

Команды:
    python -m lab100.cli status                 — пул, адаптеры, квоты
    python -m lab100.cli search  "вопрос" [--limit 5] [--no-llm]
    python -m lab100.cli ask     "вопрос" [--limit 5] [--capability web-search|deep-research|code|answer]
    python -m lab100.cli chat    "текст" [--capability code]
    python -m lab100.cli scrape  "URL"
    python -m lab100.cli providers             — какие адаптеры активны и почему
"""

from __future__ import annotations

import argparse
import os
import sys
import tomllib

from .orchestrator import PoolOrchestrator
from .providers import build_llm_adapter, build_search_adapter
from .registry import Agent, KIND_LLM, KIND_SEARCH, Registry
from .token_tracker import TokenTracker

DEFAULT_CONFIG = "config/agents.toml"


def _utf8() -> None:
    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")


def load_config(path: str) -> dict:
    try:
        with open(path, "rb") as fh:
            return tomllib.load(fh)
    except FileNotFoundError:
        return {}


def build(path: str = DEFAULT_CONFIG) -> PoolOrchestrator:
    cfg = load_config(path)
    providers = cfg.get("provider", {})
    quota_cfg = cfg.get("quota", {})
    registry = Registry()
    notes: list[str] = []

    for name, section in providers.items():
        kind = section.get("kind", KIND_LLM)
        capabilities = frozenset(section.get("capabilities", []))
        priority = int(section.get("priority", 0))
        try:
            adapter = (
                build_search_adapter(name, section) if kind == KIND_SEARCH else build_llm_adapter(name, section)
            )
            adapter.init()
        except Exception as exc:
            notes.append(f"- {name}: отключён — {exc}")
            continue
        registry.register(Agent(name, kind, capabilities, priority, adapter))

    # Один непустой пул каждого рода — иначе нечего выбирать.
    if not registry.by_kind(KIND_SEARCH):
        notes.append("! В пуле нет поисковых агентов (нужен firecrawl с ключом).")
    if not registry.by_kind(KIND_LLM):
        notes.append("! В пуле нет LLM-агентов (нужен ollama или gemini).")

    tracker = TokenTracker.from_config(quota_cfg)
    orch = PoolOrchestrator(registry, tracker)
    orch._notes = notes  # type: ignore[attr-defined]
    return orch


def _fmt_result(res) -> str:
    lines = []
    lines.append(f"категория: {res.capability} | источник: {res.provider or '-'}")
    lines.append(f"попытки: {', '.join(res.attempts) or '-'}")
    if res.metrics:
        m = ", ".join(f"{k}={v}" for k, v in res.metrics.items())
        lines.append(f"метрики: {m}")
    if res.sources:
        lines.append("источники:")
        for src in res.sources[:10]:
            lines.append(f"  [{src.position}] {src.title}")
            lines.append(f"       {src.url}")
    if res.answer:
        lines.append("ответ:")
        lines.append(res.answer)
    if res.scraped:
        body = res.scraped if len(res.scraped) <= 4000 else res.scraped[:4000] + f"\n…(ещё {len(res.scraped) - 4000} симв.)"
        lines.append("markdown:")
        lines.append(body)
    if not res.ok:
        lines.append(f"ОШИБКА: {res.error}")
    return "\n".join(lines)


def cmd_status(orch: PoolOrchestrator, _args: argparse.Namespace) -> int:
    print(f"Агентов в пуле: {len(orch.registry)}")
    for agent in orch.registry.agents():
        caps = ", ".join(sorted(agent.capabilities)) or "-"
        rem = orch.tracker.remaining(agent.name)
        rem_str = f"{rem:,.0f}" if rem != float("inf") else "∞"
        print(f"  [{agent.kind:<6}] {agent.name:<16} квота: {rem_str:>10} | {caps}")
    notes = getattr(orch, "_notes", [])
    for note in notes:
        print(note)
    return 0


def cmd_providers(orch: PoolOrchestrator, _args: argparse.Namespace) -> int:
    print("Активные адаптеры:")
    for agent in orch.registry.agents():
        print(f"  {agent.name} ({agent.kind}): {type(agent.adapter).__name__}")
    notes = getattr(orch, "_notes", [])
    if notes:
        print("Прочие:")
        for note in notes:
            if note.startswith("-"):
                print(f"  {note}")
    return 0


def cmd_search(orch: PoolOrchestrator, args: argparse.Namespace) -> int:
    res = orch.run(args.query, capability="web-search", limit=args.limit or 5, with_llm=not args.no_llm, force_llm=args.llm, force_search=args.search)
    print(_fmt_result(res))
    return 0 if res.ok else 1


def cmd_ask(orch: PoolOrchestrator, args: argparse.Namespace) -> int:
    res = orch.run(args.question, capability=args.capability, limit=args.limit or 5, with_llm=True, force_llm=args.llm, force_search=args.search)
    print(_fmt_result(res))
    return 0 if res.ok else 1


def cmd_chat(orch: PoolOrchestrator, args: argparse.Namespace) -> int:
    res = orch.run(args.text, capability=args.capability or "answer", with_llm=True, force_llm=args.llm)
    print(_fmt_result(res))
    return 0 if res.ok else 1


def cmd_scrape(orch: PoolOrchestrator, args: argparse.Namespace) -> int:
    res = orch.run_scrape(args.url, force=args.search)
    print(_fmt_result(res))
    return 0 if res.ok else 1


def main(argv: list[str] | None = None) -> int:
    _utf8()
    parser = argparse.ArgumentParser(prog="lab100", description="Пул бесплатных ИИ-агентов")
    parser.add_argument("--config", default=DEFAULT_CONFIG, help="путь к agents.toml")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="состояние пула и квот").set_defaults(func=cmd_status)
    sub.add_parser("providers", help="активные адаптеры").set_defaults(func=cmd_providers)

    p_search = sub.add_parser("search", help="веб-поиск")
    p_search.add_argument("query")
    p_search.add_argument("--limit", type=int, default=5)
    p_search.add_argument("--no-llm", action="store_true")
    p_search.add_argument("--search", metavar="ПРОВАЙДЕР", default=None, help="принудительно search-провайдера")
    p_search.add_argument("--llm", metavar="ПРОВАЙДЕР", default=None, help="принудительно LLM-провайдера")
    p_search.set_defaults(func=cmd_search)

    p_ask = sub.add_parser("ask", help="поиск + ответ LLM")
    p_ask.add_argument("question")
    p_ask.add_argument("--limit", type=int, default=5)
    p_ask.add_argument("--capability", default="web-search",
                      choices=["web-search", "deep-research", "answer", "code"])
    p_ask.add_argument("--search", metavar="ПРОВАЙДЕР", default=None, help="принудительно search-провайдера")
    p_ask.add_argument("--llm", metavar="ПРОВАЙДЕР", default=None, help="принудительно LLM-провайдера")
    p_ask.set_defaults(func=cmd_ask)

    p_chat = sub.add_parser("chat", help="только LLM")
    p_chat.add_argument("text")
    p_chat.add_argument("--capability", default="answer", choices=["answer", "code"])
    p_chat.add_argument("--llm", metavar="ПРОВАЙДЕР", default=None, help="принудительно LLM-провайдера")
    p_chat.set_defaults(func=cmd_chat)

    p_scrape = sub.add_parser("scrape", help="извлечь markdown по URL")
    p_scrape.add_argument("url")
    p_scrape.add_argument("--search", metavar="ПРОВАЙДЕР", default=None, help="принудительно search-провайдера")
    p_scrape.set_defaults(func=cmd_scrape)

    args = parser.parse_args(argv)
    orch = build(args.config)
    try:
        return args.func(orch, args)
    except Exception as exc:
        print(f"ОШИБКА: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())