"""Общие фикстуры харнесса.

Главная — `vault_pair`: синтетическая пара «вольт ↔ клон». Вольт владельца
тесты не читают и не правят (ADR-053 §1.4, CLAUDE.md «в вольт не писать»),
поэтому диспозиция infra-детекторов меряется на паре, собранной здесь:
bare-origin → рабочая копия «вольта» → клон. Заглушки реестра и сценариев тоже
лежат во временном каталоге; манифест и фикстуры берутся настоящие — мета-тест
в `--full` должен идти по действительным чекерам полосы.
"""

from __future__ import annotations

import os
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CONTRACT = "01-theygrow/operations/theygrow-delivery-contract.md"
PROMPT_KIT = "01-theygrow/operations/theygrow-delivery-prompt-kit.md"
LOG = "00-system/log.md"

CONTRACT_TEXT = """# Контракт доставки (синтетический)

## §11 Механика хода

**Провенанс утверждений оркестратора (добавлено 2026-08-18).**
Утверждение о состоянии внешнего носителя опирается на выдачу этого хода.
Расширение 2026-08-25: идентификатор — класс.

**Исполнимость shell-блока (добавлено 2026-08-18).**
Блок исполняется целиком, без правки руками.
"""

PROMPT_KIT_TEXT = """# Промпт-кит (синтетический)

## Правила использования

- **Sources-трейлер обязателен.**
  Ход, кодирующий решение вольта, несёт трейлер Sources.
"""

LOG_TEXT = """# Лог полосы

2026-09-01. Запись оркестратора. Ни одно правило реестра на этот файл не ссылается.
"""

GIT_ENV = {
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_AUTHOR_NAME": "harness",
    "GIT_AUTHOR_EMAIL": "harness@example.invalid",
    "GIT_COMMITTER_NAME": "harness",
    "GIT_COMMITTER_EMAIL": "harness@example.invalid",
}


def git(repo: str, *args: str) -> str:
    """Вызов git в названном репозитории; падает с текстом stderr при отказе."""
    env = dict(os.environ)
    env.update(GIT_ENV)
    p = subprocess.run(["git", "-C", repo, *args], capture_output=True,
                       text=True, env=env)
    if p.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} → {p.returncode}: {p.stderr}")
    return p.stdout


def write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


class VaultPair:
    """Синтетическая пара вольта: origin (bare) ↔ master (рабочая копия) ↔ clone."""

    def __init__(self, tmp_path):
        self.base = str(tmp_path)
        self.origin = os.path.join(self.base, "origin.git")
        self.master = os.path.join(self.base, "master")
        self.clone = os.path.join(self.base, "clone")
        self.reports = os.path.join(self.base, "reports")
        self.registry_path = os.path.join(self.base, "registry.yaml")
        self.scenarios_dir = os.path.join(self.base, "scenarios")

        subprocess.run(["git", "init", "--bare", "-b", "main", self.origin],
                       check=True, capture_output=True)
        subprocess.run(["git", "clone", self.origin, self.master],
                       check=True, capture_output=True)
        write(os.path.join(self.master, CONTRACT), CONTRACT_TEXT)
        write(os.path.join(self.master, PROMPT_KIT), PROMPT_KIT_TEXT)
        write(os.path.join(self.master, LOG), LOG_TEXT)
        git(self.master, "add", "-A")
        git(self.master, "commit", "-m", "канон")
        git(self.master, "push", "-u", "origin", "main")
        subprocess.run(["git", "clone", self.origin, self.clone],
                       check=True, capture_output=True)
        os.makedirs(self.reports, exist_ok=True)
        self._write_registry_and_scenarios()

    # ── содержимое ──────────────────────────────────────────────────────
    def hash_of(self, rel_file: str, heading: str) -> str:
        from linter import canon
        digest, _ = canon.hash_rule(self.clone, rel_file, heading)
        return digest

    def _write_registry_and_scenarios(self) -> None:
        h1 = self.hash_of(CONTRACT, "Провенанс утверждений оркестратора (добавлено 2026-08-18).")
        h2 = self.hash_of(PROMPT_KIT, "Sources-трейлер обязателен.")
        assert "UNRESOLVED" not in (h1, h2), (h1, h2)
        self.hashes = {"R-T-001": h1, "R-T-002": h2}
        write(self.registry_path,
              "version: 0\n"
              'computed_at: "2026-09-01T00:00:00Z"\n'
              "rules:\n"
              "  - rule_id: R-T-001\n"
              "    source:\n"
              f"      file: {CONTRACT}\n"
              '      heading: "Провенанс утверждений оркестратора (добавлено 2026-08-18)."\n'
              f'    content_hash: "{h1}"\n'
              "  - rule_id: R-T-002\n"
              "    source:\n"
              f"      file: {PROMPT_KIT}\n"
              '      heading: "Sources-трейлер обязателен."\n'
              f'    content_hash: "{h2}"\n')
        for num, (rid, digest) in enumerate(self.hashes.items(), start=1):
            write(os.path.join(self.scenarios_dir, f"s{num:02d}.yaml"),
                  f"id: S-T{num}\n"
                  "rule_refs:\n"
                  f"  - rule_id: {rid}\n"
                  "    role: primary\n"
                  f'    hash_at_binding: "{digest}"\n'
                  "status: active\n")

    def config(self) -> dict:
        """Конфиг прогона под пару. Путей вольта в нём нет: они приходят только
        из окружения (run.ENV_CLONE / run.ENV_MASTER), и фикстура `vault_pair`
        ставит их на эту пару."""
        return {
            "vault": {"canon_paths": ["00-system/", "01-theygrow/operations/"]},
            "paths": {"registry": self.registry_path, "scenarios": self.scenarios_dir,
                      "manifest": "linter/manifest.yaml", "fixtures": "linter/fixtures",
                      "reports": self.reports},
            "canon": {"section_parser": "bold_lead_v1"},
            "observations": [],
        }

    # ── мутации ─────────────────────────────────────────────────────────
    def touch(self, rel_path: str, text: str = "\nправка мутации\n") -> None:
        """Незакоммиченная правка файла в рабочей копии «вольта»."""
        with open(os.path.join(self.master, rel_path), "a", encoding="utf-8") as fh:
            fh.write(text)

    def commit(self, message: str = "мутация") -> str:
        git(self.master, "add", "-A")
        git(self.master, "commit", "-m", message)
        return git(self.master, "rev-parse", "HEAD").strip()

    def push(self) -> None:
        git(self.master, "push", "origin", "main")

    def clone_fetch(self) -> None:
        git(self.clone, "fetch", "--quiet")

    def clone_pull(self) -> None:
        git(self.clone, "pull", "--ff-only", "--quiet")

    def clone_head(self) -> str:
        return git(self.clone, "rev-parse", "HEAD").strip()


@pytest.fixture
def vault_pair(tmp_path, monkeypatch):
    """Пара вольта плюс окружение, её адресующее.

    Переменные ставятся здесь, а не в конфиге: пути вольта берутся только из
    окружения, и у владельца они заданы на живой вольт. Без подмены тест ушёл
    бы читать вольт владельца — ровно то, что запрещено (CLAUDE.md, «в вольт не
    писать»; вольт владельца тесты не читают).
    """
    import run
    pair = VaultPair(tmp_path)
    monkeypatch.setenv(run.ENV_CLONE, pair.clone)
    monkeypatch.setenv(run.ENV_MASTER, pair.master)
    return pair


def parse_run_output(text: str) -> dict:
    """Разбор выдачи `main`: перечни красных и предупреждений сводки.

    Меряется то же, что читает владелец, — печатные строки прогона, а не
    внутренние структуры.
    """
    reds: list[str] = []
    warns: list[str] = []
    bucket = None
    for line in text.splitlines():
        if line.startswith("- красных:"):
            bucket = reds
            continue
        if line.startswith("- предупреждений:"):
            bucket = warns
            continue
        if line.startswith("- вердикт:"):
            bucket = None
            continue
        if bucket is not None and line.startswith("  - "):
            bucket.append(line[4:])
    return {"reds": reds, "warnings": warns}


def infra_lines(lines: list[str], status: str) -> list[str]:
    return [ln for ln in lines if ln.startswith(f"[infra] {status}:")]


@pytest.fixture
def full_run(monkeypatch, capsys):
    """Прогон `run.main(["--full","--no-report"])` на подменённом конфиге.

    Возвращает (код выхода, {reds, warnings}) — единица измерения диспозиции
    «на прогон --full».
    """
    def _run(config: dict):
        import run
        monkeypatch.setattr(run, "load_config", lambda: config)
        code = run.main(["--full", "--no-report"])
        out = capsys.readouterr().out
        return code, parse_run_output(out), out
    return _run
