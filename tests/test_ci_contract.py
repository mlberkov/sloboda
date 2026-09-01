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
    """`--full` на раннере без вольта краснеет clone_missing: его тут нет."""
    with open(CI, encoding="utf-8") as fh:
        text = fh.read()
    steps = workflow()["jobs"][REQUIRED_CHECK]["steps"]
    commands = " ".join(s.get("run", "") for s in steps)
    assert "run.py --fast" in commands
    assert "--full" not in commands
    assert "vaults/theygrow-vault" in text  # шаг «вольта нет физически»


def test_ci_installs_dev_requirements():
    steps = workflow()["jobs"][REQUIRED_CHECK]["steps"]
    commands = " ".join(s.get("run", "") for s in steps)
    assert "requirements-dev.txt" in commands
    assert "pre-commit run --all-files" in commands
