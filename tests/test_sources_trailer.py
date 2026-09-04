"""Sources-трейлер: инвариант вместо формы инцидента, плюс «что ограничивает».

Повод (реестр правок канона §D, 2026-08-31, третий случай): триггер «упоминание
ADR-» не видел handoff, кодирующий решение вольта словами «реестр §D», «несущий
документ §6», «контракт §11», «owner decision 2=а». Вторая находка заведена по
тексту самого пункта prompt-kit: якорь несёт «одну строку о том, что он
ограничивает», и якорь без неё называет источник, не называя связи.

Единица диспозиции — «на артефакт `--fast`». Мутации меряются вызовом
`run.main`, единичные свойства — вызовом `check` на строке.
"""

from __future__ import annotations

import os

import pytest
import yaml

import run
from linter.checkers import sources_trailer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RED = os.path.join(ROOT, "linter", "fixtures", "red", "sources_trailer.md")
GREEN = os.path.join(ROOT, "linter", "fixtures", "green", "sources_trailer.md")


@pytest.fixture(scope="module")
def config() -> dict:
    """Конфиг чекера из манифеста: списки форм — данные, а не копия в тесте."""
    with open(os.path.join(ROOT, "linter", "manifest.yaml"), encoding="utf-8") as fh:
        manifest = yaml.safe_load(fh) or {}
    shared = manifest.get("shared") or {}
    entry = next(c for c in manifest["checkers"] if c["name"] == "sources_trailer")
    return {**shared, **(entry.get("config") or {})}


def packet(body: str) -> str:
    """Packet-handoff вокруг тела: задача плана по репозиторию."""
    return ("```text\nЗадача: собрать план реализации пакета по репозиторию "
            "altrego, ветка chore/x.\n" + body + "\nФормат выхода: план.\n```\n")


def findings(text: str, config: dict) -> list[str]:
    return [f.message for f in sources_trailer.check(text, config)]


def fixture_lines(capsys, path: str) -> tuple[int, list[str]]:
    code = run.main(["--fast", path, "--no-report"])
    out = capsys.readouterr().out
    listed = out.split("## Находки", 1)[1].split("## Сводка", 1)[0]
    return code, [ln for ln in listed.splitlines() if " sources_trailer " in ln]


# ───────────────────── мутации, на артефакт --fast ───────────────────────

def test_red_fixture_carries_both_mutations(capsys):
    """Handoff с решением вольта словами без Sources — и якорь без связи.

    Базовая диспозиция до правки: 1 находка (по слову «ADR-053» в прежней
    редакции фикстуры); теперь предмет другой, и находок две — по одной на
    мутацию.
    """
    code, lines = fixture_lines(capsys, RED)
    assert code == 1
    assert len(lines) == 2
    assert not any("anchor_without_constraint" in ln for ln in lines[:1])
    assert "anchor_without_constraint" in lines[1]


def test_green_fixture_is_silent(capsys):
    """Трейлер с якорями и строками «что ограничивает»; DR-промпт без пакета."""
    code, lines = fixture_lines(capsys, GREEN)
    assert (code, lines) == (0, [])


# ─────────────────── ссылка на носитель вольта: формы ────────────────────

@pytest.mark.parametrize("said", [
    "- Долг реестра 2026-08-31: триггер по форме.",
    "- Несущий документ §6, слой 0, пункт 5.",
    "- Контракт §11 требует диспозиции.",
    "- owner decision 2=а: гейт CI обязателен.",
    "- owner-акт 2026-09-04 разрешил расширение механики.",
    "- ADR-054 задаёт слой 0 контроля.",
])
def test_vault_reference_forms_trigger(config, said):
    """Решение вольта, названное любой формой, требует трейлера."""
    assert len(findings(packet(said), config)) == 1


def test_bare_section_without_document_is_not_a_vault_reference(config):
    """Голый §N решением вольта не является: § сторонней бумаги — не канон."""
    assert findings(packet("- Сверено с §4 стороннего стандарта."), config) == []


def test_non_packet_prompt_is_allowed(config):
    """«Допустимое попадание»: DR-промпт с ADR, но без задачи по репозиторию."""
    text = ("```text\nТема: трассировка решений до артефакта.\n"
            "Принимай как данность: внутренний адрес — ADR-053, срез 2026-09-04.\n"
            "Формат выхода: сводка на десять строк.\n```\n")
    assert findings(text, config) == []


# ───────────────────────── строка «что ограничивает» ─────────────────────

def test_anchor_with_constraint_is_silent(config):
    body = ("- Долг реестра 2026-08-31.\n\nSources\n"
            "- ADR-053 §1.4 — ярусы подписи: ограничивает правку без владельца.\n")
    assert findings(packet(body), config) == []


def test_bare_anchor_is_red(config):
    body = "- Долг реестра 2026-08-31.\n\nSources\n- ADR-053 §1.4\n"
    (message,) = findings(packet(body), config)
    assert "anchor_without_constraint" in message
    assert "ADR-053" in message


def test_short_tail_after_dash_is_not_a_constraint(config):
    """Тире с двумя словами связи не называет: порог — значимые символы."""
    body = "- Долг реестра 2026-08-31.\n\nSources\n- ADR-053 §1.4 — да\n"
    assert len(findings(packet(body), config)) == 1


def test_unfilled_template_slot_is_not_an_anchor(config):
    """Слот шаблона маскируется: якорь внутри него — имя формы, а не якорь."""
    body = ("- Долг реестра 2026-08-31.\n\nSources\n"
            "- [ADR/PDR/spec ids + one line each on what it constrains]\n")
    (message,) = findings(packet(body), config)
    assert "якорей после неё — 0" in message


def test_trailer_window_covers_four_anchors(config):
    """Область трейлера — до пустой строки: список из четырёх якорей цел.

    Прежнее окно в 3 строки обрезало трейлер, и счёт вёлся по трети списка.
    """
    anchors = "\n".join(
        f"- ADR-05{i} §1.{i} — ограничивает пункт {i} этого пакета." for i in range(4))
    body = f"- Долг реестра 2026-08-31.\n\nSources\n{anchors}\n"
    assert findings(packet(body), config) == []
