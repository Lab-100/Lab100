"""Провайдер Docker Agent (Гордон): локальный LLM через `docker agent run`.

Обвязывает CLI-плагин Docker `docker agent` интерфейсом LLMAdapter пула Lab100.
Исполнитель — Docker Model Runner (бесплатный локальный слой, по умолчанию
`--model local`, smollm2). При этом gordon.ps1/Gordon остаётся отдельным
роутером облачных провайдеров; этот адаптер вводит его в группу перебора пула,
где выбор между ollama/gemini/docker-agent делает router.py по приоритету и
квоте, а cooldown при недоступности обрабатывает TokenTracker.

docker-клиент ищется: env DOCKER_EXE → PATH → известные пути Docker Desktop.
Плагин `docker agent` проверяется в init(); при его отсутствии адаптер
исключается из пула как «недоступен» (не падает, остальные продолжают работать).
"""

from __future__ import annotations

import os
import shutil
import subprocess

from .base import AdapterError, LLMAdapter

_DOCKER_PATHS = [
    r"C:\Program Files\Docker\Docker\resources\bin\docker.exe",
    r"C:\Program Files\Docker\Docker\Docker Desktop.exe",  # только для автонаходки клиента
]


def _locate_docker() -> str | None:
    exe = os.environ.get("DOCKER_EXE")
    if exe and os.path.isfile(exe):
        return exe
    found = shutil.which("docker") or shutil.which("docker.exe")
    if found:
        return found
    for p in _DOCKER_PATHS:
        if p.lower().endswith("docker.exe") and os.path.isfile(p):
            return p
    return None


def _plugin_candidates(docker_exe: str) -> list[str]:
    """Каталоги, где Docker ищет cli-плагины (docker-agent)."""
    base = os.path.dirname(docker_exe)
    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    pd = os.environ.get("ProgramData", r"C:\ProgramData")
    user = os.path.expanduser("~")
    return [
        os.path.join(user, ".docker", "cli-plugins"),
        os.path.join(pf, "Docker", "cli-plugins"),
        os.path.join(pd, "Docker", "cli-plugins"),
        os.path.join(base, "cli-plugins"),
        os.path.join(pf, "Docker", "Docker", "resources", "cli-plugins"),
    ]


def _has_agent_plugin(docker_exe: str) -> bool:
    for d in _plugin_candidates(docker_exe):
        for name in ("docker-agent.exe", "docker-agent"):
            if os.path.isfile(os.path.join(d, name)):
                return True
    return False


class DockerAgentLLM(LLMAdapter):
    name = "docker-agent"

    def __init__(
        self,
        agent_file: str | None = None,
        model: str = "local",
        docker_exe: str | None = None,
        timeout_s: int = 600,
    ) -> None:
        self.agent_file = agent_file or os.path.join(
            os.path.expanduser("~"), ".agents", "gordon.yaml"
        )
        self.model = model
        self.docker_exe = docker_exe or _locate_docker()
        self.timeout_s = timeout_s

    def init(self) -> None:
        if not self.docker_exe:
            raise RuntimeError("docker-agent: docker-клиент не найден (DOCKER_EXE/PATH/Docker Desktop)")
        if not _has_agent_plugin(self.docker_exe):
            raise RuntimeError(
                "docker-agent: плагин `docker agent` не установлен (подключается gordon-setup.ps1)"
            )
        if not os.path.isfile(self.agent_file):
            raise RuntimeError(
                f"docker-agent: нет конфига Гордона {self.agent_file} (gordon-setup.ps1)"
            )

    def chat(
        self, prompt: str, system: str = "", max_tokens: int = 1200
    ) -> tuple[str, dict]:
        full = f"[System]\n{system}\n\n[Задача]\n{prompt}" if system else prompt
        cmd = [
            self.docker_exe,
            "agent",
            "run",
            self.agent_file,
            "--model",
            self.model,
            "--exec",
            full,
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=self.timeout_s)  # noqa: S603
        except subprocess.TimeoutExpired:
            raise AdapterError(f"docker-agent: таймаут {self.timeout_s}s") from None

        out = (proc.stdout or b"").decode("utf-8", errors="replace").strip()
        err = (proc.stderr or b"").decode("utf-8", errors="replace").strip()

        combined = (out + "\n" + err).lower()
        if proc.returncode != 0:
            raise AdapterError(f"docker-agent rc={proc.returncode}: {err[-500:]}")
        if (
            any(mark in combined for mark in ("rate limit", "429", "quota", "credit", "insufficient"))
            and not out
        ):
            raise AdapterError(f"docker-agent: {err[-500:] or 'rate limit'}")
        if not out:
            raise AdapterError(f"docker-agent: пустой ответ ({err[-200:]})")

        metrics = {
            "model": self.model,
            "tokens": len(out) // 4,
            "credits": 0,
            "provider": self.name,
        }
        return out, metrics