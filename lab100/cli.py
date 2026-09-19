"""CLI для пула агентов.

Команды:
    python -m lab100.cli status            — пул и остатки квот
    python -m lab100.cli run --task "..." [--capability web-search] [--dry-run]
"""

from __future__ import annotations

import argparse
import sys
import tomllib

from lab100.orchestrator import PoolOrchestrator
from lab100.registry import Registry
from lab100.token_tracker import TokenTracker

DEFAULT_CONFIG = "config/agents.toml"


def load_config(path: str) -> dict:
    try:
        with open(path, "rb") as fh:
            return tomllib.load(fh)
    except FileNotFoundError:
        return {}


def build(path: str) -> PoolOrchestrator:
    cfg = load_config(path)
    registry = Registry.from_config(cfg.get("provider", {}))
    tracker = TokenTracker.from_config(cfg.get("quota", {}))
    return PoolOrchestrator(registry, tracker)


def cmd_status(orchestrator: PoolOrchestrator, _args: argparse.Namespace) -> int:
    print(f"Агентов в пуле: {len(orchestrator.registry)}")
    for agent in orchestrator.registry.agents():
        caps = ", ".join(sorted(agent.capabilities)) or "-"
        remaining = orchestrator.tracker.remaining(agent.provider)
        print(f"  {agent.name:<16} комп: {caps:<40} квота: {remaining}")
    return 0


def cmd_run(orchestrator: PoolOrchestrator, args: argparse.Namespace) -> int:
    result = orchestrator.run(args.task, args.capability, dry_run=args.dry_run)
    print(f"Задача: {result.task}")
    print(f"Категория: {result.capability}")
    print(f"Попытки: {', '.join(result.attempts) or '-'}")
    if result.agent:
        print(f"Агент: {result.agent.name} ({result.agent.provider})")
    if result.ok:
        print(f"OK: {result.output}")
        return 0
    print(f"Ошибка: {result.error}")
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lab100", description="Пул бесплатных ИИ-агентов")
    parser.add_argument("--config", default=DEFAULT_CONFIG, help="путь к agents.toml")
    sub = parser.add_subparsers(dest="command", required=True)

    p_status = sub.add_parser("status", help="состояние пула и квот")
    p_status.set_defaults(func=cmd_status)

    p_run = sub.add_parser("run", help="выполнить задачу")
    p_run.add_argument("--task", required=True, help="текст задачи")
    p_run.add_argument("--capability", default="web-search", help="необходимая компетенция")
    p_run.add_argument("--dry-run", action="store_true", help="только выбрать агента, не вызывать")
    p_run.set_defaults(func=cmd_run)

    args = parser.parse_args(argv)
    orchestrator = build(args.config)
    return args.func(orchestrator, args)


if __name__ == "__main__":
    sys.exit(main())