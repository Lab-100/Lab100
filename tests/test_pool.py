"""Smoke-тесты пула: registry → tracker → router → orchestrator."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lab100.orchestrator import PoolOrchestrator
from lab100.registry import Agent, Registry
from lab100.router import RouterError, pick
from lab100.token_tracker import TokenTracker

from lab100.cli import load_config, build  # noqa: E402


def registry_fixture() -> Registry:
    return Registry(
        [
            Agent("gemini", "gemini", frozenset({"web-search", "vision"}), priority=30),
            Agent("groq", "groq", frozenset({"web-search", "code"}), priority=25),
            Agent("ollama", "ollama", frozenset({"code"}), priority=1),
        ]
    )


def tracker_fixture() -> TokenTracker:
    t = TokenTracker()
    t._quotas["gemini"] = t.ensure("gemini")
    t._quotas["gemini"].limit = 100
    t._quotas["groq"] = t.ensure("groq")
    t._quotas["groq"].limit = 50
    return t


def test_pick_by_capability():
    reg, tr = registry_fixture(), tracker_fixture()
    a = pick(reg, tr, "web-search")
    assert a.name == "gemini"


def test_pick_no_capability():
    reg, tr = registry_fixture(), tracker_fixture()
    with pytest.raises(RouterError):
        pick(reg, tr, "vision_unknown")


def test_pick_excludes_locked():
    reg, tr = registry_fixture(), tracker_fixture()
    tr.ensure("gemini").used = 95  # выше порога reserve (10%)
    a = pick(reg, tr, "web-search")
    assert a.name == "groq"


def test_cooldown_blocks_provider():
    reg, tr = registry_fixture(), tracker_fixture()
    tr.cooldown("groq", 1000)
    a = pick(reg, tr, "web-search")
    assert a.name == "gemini"


def test_orchestrator_dry_run():
    reg, tr = registry_fixture(), tracker_fixture()
    orch = PoolOrchestrator(reg, tr)
    res = orch.run("что нового?", capability="web-search", dry_run=True)
    assert res.ok
    assert res.agent.name == "gemini"


def test_orchestrator_fallback_after_exhaustion():
    reg, tr = registry_fixture(), tracker_fixture()

    class FailGemini:
        def run(self, provider, prompt):
            if provider == "gemini":
                raise RuntimeError("429 rate limit")
            return "ok-from-groq"

    orch = PoolOrchestrator(reg, tr, FailGemini())
    res = orch.run("поиск", capability="web-search")
    assert res.ok
    assert res.agent.name == "groq"
    assert res.attempts == ["gemini", "groq"]


def test_config_loads():
    cfg = load_config("config/agents.toml")
    assert "provider" in cfg and "quota" in cfg


def test_build_from_config():
    orch = build("config/agents.toml")
    assert len(orch.registry) >= 5
    assert orch.tracker.remaining("gemini") == 1500