"""Контракт CI: имя обязательной проверки.

Зелёный CI — обязательное условие слияния в main (owner decision 2=да), и
обязательной проверкой branch protection названа строка `gate`. Переименование
job'а или заведение матрицы сменило бы имя проверки молча: защита осталась бы
формально включённой, а слияние прошло бы мимо гейта. Поэтому имя закреплено
здесь — статическое свойство файла, и сверка подстроки для него законна.
"""

from __future__ import annotations

import os

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CI = os.path.join(ROOT, ".github", "workflows", "ci.yml")

REQUIRED_CHECK = "gate"


def workflow() -> dict:
    with open(CI, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def test_job_name_is_gate():
    jobs = workflow()["jobs"]
    assert list(jobs) == [REQUIRED_CHECK]
    assert jobs[REQUIRED_CHECK]["name"] == REQUIRED_CHECK


def test_no_matrix_in_job():
    """Матрица дописала бы параметры в имя проверки — `gate (3.12)`."""
    assert "strategy" not in workflow()["jobs"][REQUIRED_CHECK]


def test_triggers_are_push_and_pull_request():
    # `on:` в YAML 1.1 разбирается как булево True — ключ ищется по обоим видам.
    wf = workflow()
    triggers = wf.get("on", wf.get(True))
    assert set(triggers) == {"push", "pull_request"}


def test_full_run_is_not_in_ci():
    """`--full` на раннере краснеет vault_env_unset: вольт тут не адресован."""
    steps = workflow()["jobs"][REQUIRED_CHECK]["steps"]
    commands = " ".join(s.get("run", "") for s in steps)
    assert "run.py --fast" in commands
    assert "--full" not in commands


def test_ci_asserts_both_vault_variables_are_unset():
    """Шаг «вольт не адресован»: обе переменные проверяются, обе — по имени.

    Проверка одной переменной оставила бы вторую дверь открытой, а её отсутствие
    читалось бы как доказательство: зелёный job говорил бы «вольта нет», измерив
    половину.
    """
    steps = workflow()["jobs"][REQUIRED_CHECK]["steps"]
    commands = " ".join(s.get("run", "") for s in steps)
    for name in ("ALTREGO_VAULT_CLONE", "ALTREGO_VAULT_MASTER"):
        assert f'test -z "${{{name}:-}}"' in commands


def test_ci_carries_no_vault_literal():
    """Пути машины владельца ушли из репозитория — в гейте их тоже нет."""
    with open(CI, encoding="utf-8") as fh:
        text = fh.read()
    assert "theygrow-vault" not in text
    assert "Obsidian" not in text


def test_job_has_timeout_minutes():
    """Слой защиты от невозвращающегося вызова: лимит чекера ловит медленный
    чекер, но не вечный — регэксп на уровне C сигналом не прерывается. Ключ
    статический, поэтому и меряется статически."""
    job = workflow()["jobs"][REQUIRED_CHECK]
    limit = job.get("timeout-minutes")
    assert isinstance(limit, int) and 1 <= limit <= 30, limit


def test_repo_texts_carry_no_stale_names():
    """Имя полосы — altrego; тройка (Sloboda/Starosta/Artel) снята.

    Статическое свойство файлов, поэтому сверка подстроки здесь законна.
    """
    for name in ("README.md", "CLAUDE.md"):
        with open(os.path.join(ROOT, name), encoding="utf-8") as fh:
            text = fh.read().lower()
        for stale in ("sloboda", "starosta", "artel"):
            assert stale not in text, f"{name}: {stale}"


def test_ci_installs_dev_requirements():
    steps = workflow()["jobs"][REQUIRED_CHECK]["steps"]
    commands = " ".join(s.get("run", "") for s in steps)
    assert "requirements-dev.txt" in commands
    assert "pre-commit run --all-files" in commands
