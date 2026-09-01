"""Разбор канона и content_hash: `linter/canon.py`.

Тесты исполняют разбор, а не сканируют его. Хэш держится эталонной константой:
молчаливая смена нормализации сменила бы смысл всех `content_hash` реестра, и
поймать это можно только сверкой с числом, посчитанным до правки.
"""

from __future__ import annotations

import os

from linter import canon

SAMPLE = """# Раздел

Проза до пункта.

**Первый пункт.**
Тело первого пункта.
**Расширение первого пункта.**
Продолжение того же абзац-блока.

**Второй пункт.**
Тело второго.

## Правила использования

- **Списочный пункт.**
  Тело списочного пункта.
- **Второй списочный.**
  Тело второго списочного.
"""


def titles(text=SAMPLE):
    return [s.title for s in canon.parse_sections(text)]


def test_paragraph_lead_is_section():
    assert "Первый пункт." in titles()
    assert "Второй пункт." in titles()


def test_continuation_stays_inside_section():
    """Уровень 3 — продолжение абзац-блока: собственной секции не образует."""
    assert "Расширение первого пункта." not in titles()
    first = canon.find_section(SAMPLE, "Первый пункт.")
    assert "Расширение первого пункта." in first.text
    assert "Тело первого пункта." in first.text


def test_list_lead_is_own_section():
    """bold_lead_v1: `- **…**` — самостоятельный пункт, а не продолжение."""
    assert "Списочный пункт." in titles()
    assert "Второй списочный." in titles()
    s = canon.find_section(SAMPLE, "Списочный пункт.")
    assert "Тело списочного пункта." in s.text
    assert "Второй списочный." not in s.text


def test_section_bounds_are_one_indexed():
    lines = SAMPLE.split("\n")
    s = canon.find_section(SAMPLE, "Второй пункт.")
    assert lines[s.start_line - 1].startswith("**Второй пункт.")
    # Секция кончается на последней непустой строке до следующего заголовка.
    assert lines[s.end_line - 1].strip() == "Тело второго."


def test_heading_closes_section():
    s = canon.find_section(SAMPLE, "Второй пункт.")
    assert "Правила использования" not in s.text


def test_normalize_rstrips_and_drops_trailing_blanks():
    assert canon.normalize(["a  ", "b\t", "", "  "]) == "a\nb"


def test_normalize_keeps_inner_blanks():
    assert canon.normalize(["a", "", "b"]) == "a\n\nb"


def test_crlf_and_cr_do_not_change_hash():
    lf = canon.parse_sections(SAMPLE)
    crlf = canon.parse_sections(SAMPLE.replace("\n", "\r\n"))
    cr = canon.parse_sections(SAMPLE.replace("\n", "\r"))
    assert [s.content_hash for s in lf] == [s.content_hash for s in crlf]
    assert [s.content_hash for s in lf] == [s.content_hash for s in cr]


def test_hash_golden():
    """Эталон: sha256 нормализованной секции. Меняется только вместе с версией
    разбора — молча сменить нормализацию значит сменить смысл реестра."""
    s = canon.find_section(SAMPLE, "Второй пункт.")
    assert s.text == "**Второй пункт.**\nТело второго."
    assert s.content_hash == canon.sha256_text(s.text)
    assert s.content_hash == (
        "e3eea7fbb9f56e2e565923340b9263607d270b4a973f36e8fed53d24c87e5e81")


def test_trailing_whitespace_does_not_change_hash():
    dirty = SAMPLE.replace("Тело второго.", "Тело второго.   ")
    assert (canon.find_section(dirty, "Второй пункт.").content_hash
            == canon.find_section(SAMPLE, "Второй пункт.").content_hash)


def test_find_section_exact_and_prefix_fallback():
    text = "**Пункт канона (добавлено 2026-08-18 по разбору L2).**\nТело.\n"
    assert canon.find_section(text, "Пункт канона (добавлено 2026-08-18 по разбору L2).")
    # Заголовок в реестре записан без хвоста «(добавлено …)» — берётся префикс.
    assert canon.find_section(text, "Пункт канона")
    assert canon.find_section(text, "Другой пункт") is None


def test_hash_rule_on_real_file(tmp_path):
    rel_file = "01-theygrow/operations/sample.md"
    path = os.path.join(str(tmp_path), rel_file)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(SAMPLE)
    digest, section = canon.hash_rule(str(tmp_path), rel_file, "Второй пункт.")
    assert digest == canon.find_section(SAMPLE, "Второй пункт.").content_hash
    assert section.title == "Второй пункт."


def test_hash_rule_unresolved_without_file(tmp_path):
    digest, section = canon.hash_rule(str(tmp_path), "нет/такого.md", "Второй пункт.")
    assert digest == canon.UNRESOLVED and section is None


def test_hash_rule_unresolved_without_heading(tmp_path):
    rel_file = "a.md"
    with open(os.path.join(str(tmp_path), rel_file), "w", encoding="utf-8") as fh:
        fh.write(SAMPLE)
    digest, section = canon.hash_rule(str(tmp_path), rel_file, "Отсутствующий пункт.")
    assert digest == canon.UNRESOLVED and section is None


def test_resolve_path_expands_user():
    assert canon.resolve_path("~/vault", "a/b.md").startswith(os.path.expanduser("~"))
