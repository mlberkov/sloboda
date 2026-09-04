"""Признак копируемого промпта: инвариант вместо формы инцидента.

Повод (реестр правок канона §D, 2026-08-31, «триггер по форме против триггера по
инварианту», третий случай): признак «блок длиннее 15 строк с адресацией
«Задача:»» описывал тот промпт, на котором чекер заводился, и не видел
DR-промпта в 12 строк с адресацией «Тема:» — наблюдено 2 находки против вилки
6–10. Длина снята с признака целиком; вместо неё три части инварианта.

Единица диспозиции — «на артефакт `--fast`»: число находок и код выхода на
конкретном файле. Мутации меряются вызовом `run.main`, а не чтением кода;
единичные свойства признака — вызовом `prompts.prompt_blocks` на строке.
"""

from __future__ import annotations

import os

import pytest
import yaml

import run

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RED = os.path.join(ROOT, "linter", "fixtures", "red", "prompt_self_assessment.md")
GREEN = os.path.join(ROOT, "linter", "fixtures", "green", "prompt_self_assessment.md")


@pytest.fixture(scope="module")
def shared() -> dict:
    """Признак промпта — данные манифеста: тесты читают их, а не копию в коде."""
    with open(os.path.join(ROOT, "linter", "manifest.yaml"), encoding="utf-8") as fh:
        return (yaml.safe_load(fh) or {}).get("shared") or {}


def blocks(text: str, shared: dict):
    from linter import prompts
    return prompts.prompt_blocks(text, shared)


def findings_of(capsys, path: str, checker: str) -> list[str]:
    code = run.main(["--fast", path, "--no-report"])
    out = capsys.readouterr().out
    listed = out.split("## Находки", 1)[1].split("## Сводка", 1)[0]
    return code, [ln for ln in listed.splitlines() if f" {checker} " in ln]


# ───────────────────── мутации, на артефакт --fast ───────────────────────

def test_dr_prompt_twelve_lines_is_red(capsys):
    """DR-промпт 12 строк с «Тема:», URL и датой, без критериев и числа.

    Базовая диспозиция до правки — 0 находок (прежний признак требовал 15 строк
    и адресации «Задача:»). Ожидание — 1 находка и код 1.
    """
    code, lines = findings_of(capsys, RED, "prompt_self_assessment")
    assert code == 1
    assert len(lines) == 1
    assert "Тема:" in lines[0]                 # свидетельство адресации
    assert "фактический параметр" in lines[0]


def test_green_cases_are_silent(capsys):
    """Go-ahead без параметров, промпт с самооценкой и блок записи в вольт."""
    code, lines = findings_of(capsys, GREEN, "prompt_self_assessment")
    assert (code, lines) == (0, [])


# ─────────────────── единичные свойства признака ─────────────────────────

def test_length_is_not_a_signal(shared):
    """Длина признаком не является ни в одну сторону."""
    short = "```text\nТема: устройство наборов.\nСрез 2026-09-04.\n```\n"
    assert len(blocks(short, shared)) == 1


def test_manifest_carries_no_dead_threshold(shared):
    """`prompt_min_lines` снят: мёртвая данная читалась бы как порог."""
    assert "prompt_min_lines" not in shared


def test_shell_block_with_task_is_not_a_prompt(shared):
    """Команда владельцу — не промпт агенту, даже со словом «Задача»."""
    text = ("```bash\n# Задача: прогнать линтер\n"
            ".venv/bin/python run.py --fast linter/fixtures/green/turn_end.md\n```\n")
    assert blocks(text, shared) == []


def test_unlabelled_command_block_is_not_a_prompt(shared):
    """Блок без метки языка из команд — тоже набор команд, а не промпт."""
    text = ("```\ncd ~/altrego\ngit status --short\n"
            ".venv/bin/python -m pytest\ngit log --oneline -3\n"
            "Задача: сверить вывод\n```\n")
    assert blocks(text, shared) == []


def test_prompt_without_parameters_is_allowed(shared):
    """«Допустимое попадание» пункта: go-ahead фактических параметров не несёт."""
    text = ("```text\nGo. Приступай к следующему пункту.\n"
            "Развилку решай сам и логируй.\nВопросов не задавай.\n```\n")
    assert blocks(text, shared) == []


@pytest.mark.parametrize("address", [
    "Тема: устройство наборов.",
    "Вопросы исследования:",
    "Your task: собрать план.",
    "Собери обзор по трём источникам.",
    "Ты — редактор внешнего документа.",
])
def test_address_forms_are_data(shared, address):
    """Формы адресации — данные манифеста: список расширяется без правки кода."""
    text = f"```text\n{address}\nСрез 2026-09-04, файл README.md.\n```\n"
    assert len(blocks(text, shared)) == 1


@pytest.mark.parametrize("parameter", [
    "https://github.com/theygrow/altrego",
    "~/altrego/linter/checkers",
    "ADR-053",
    "2026-09-04",
    "run.py",
    "ветка chore/stage-b-p2-debt",
    "16 чекеров",
])
def test_parameter_forms_are_data(shared, parameter):
    text = f"```text\nЗадача: собрать план.\nПринимай как данность: {parameter}.\n```\n"
    assert len(blocks(text, shared)) == 1


def test_block_for_owner_vault_record_is_not_a_prompt(shared):
    """Блок, готовящий запись в вольт, адресован владельцу, а не исполнителю."""
    text = ("Ниже — запись в вольт, которую делает владелец.\n\n"
            "```text\n2026-09-04. §B, рецидив: счёт 5.\n"
            "Лечение: расширение механики.\n```\n")
    assert blocks(text, shared) == []


# ───────────────────────── регрессия прежней формы ───────────────────────

def test_old_incident_form_still_caught(shared):
    """Инвариант покрывает и ту форму, на которой чекер заводился."""
    body = "\n".join(["Задача: собрать обзор по устройству наборов.",
                      "Принимай как данность: 20 сценариев, файл README.md."]
                     + [f"{i}. Пункт задания." for i in range(1, 15)])
    assert len(blocks(f"```text\n{body}\n```\n", shared)) == 1
