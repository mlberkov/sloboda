"""S-06 / К2: исполняемый блок владельцу без цитаты фактического вывода.

Опора: контракт §11, «Строка смоука — детектор и исполняется на предмете до
выдачи» (реестр: R-SMOKE-004). Пункт говорит о проверочной строке, которую
оркестратор пишет **для владельца**: перед выдачей она исполняется на текущем
предмете, и «ожидаемый вывод в handoff берётся из этого исполнения, а не из
головы»; строка, которую исполнить не на чем, помечается «не исполнялась —
первый прогон у владельца» с названным ожиданием.

Что меряется — инвариант, а не форма инцидента. Прежняя версия чекера искала
слова «строка смоука» / «проверочная строка»: это дословные примеры из текста
пункта, а не признак нарушения. В настоящих handoff-артефактах владелец не
получает слов «строка смоука» — он получает блок; калибровочный прогон
20260831T154751Z дал по этому чекеру ноль находок там, где нарушения есть.
Инвариант пункта: **владельцу выдан исполняемый блок и рядом названо ожидание
его вывода**. Признак:

  * огороженный блок в разделе «Ваши действия» — §7 требует, чтобы все действия
    владельца стояли в этом перечне, поэтому блок в нём и есть выданный
    владельцу детектор (границы раздела — те же данные, что у owner_action_block);
  * строка ожидания в том же или следующем абзаце — маркеры «Ожидаемо»,
    «Ожидаемый вывод», «Ожидание» (`expectation_patterns`, данные манифеста),
    с хвостом не короче `min_expectation_chars` значащих символов.

Два сообщения одного чекера:

  * ожидание названо, но не цитирует фактический вывод на предмете
    (`executed_patterns`) и не помечено «не исполнялась — первый прогон у
    владельца» (`not_executed_patterns`) — красный: ожидание из головы
    неотличимо от измеренного, ровно эту неотличимость пункт и запрещает;
  * блок без строки ожидания вовсе — красный отдельным сообщением: §7 требует
    от пункта назвать, «какой результат считается ожидаемым», иначе владельцу
    нечего сопоставить с выводом.

Область поиска ожидания — абзац-зачин блока (непустые строки прямо над оградой)
и `expectation_paragraphs` абзацев после закрывающей ограды, но не дальше
границы раздела, следующей ограды или markdown-заголовка: ожидание из чужого
пункта не оправдывает блок, к которому не относится.

Списки форм и границы — данные: живут в linter/manifest.yaml и расширяются без
правки этого модуля.

Чистая функция: ни сети, ни LLM, ни файловых эффектов.
"""

from __future__ import annotations

import re

from ..common import (FENCE, MD_HEADING, RED, Finding, head_sections,
                      parse_blocks, significant_chars, split_lines)

NAME = "smoke_line"

DEFAULT_SECTION = r"^[\s*_>#|-]*\**\s*Ваши\s+действия\b"
DEFAULT_SECTION_END = r"^[\s*_>#|-]*\**\s*Конец\s+хода\b"
DEFAULT_SECTION_MAX_LINES = 40
DEFAULT_EXPECTATION_PARAGRAPHS = 2

# «Фактический вывод» — вывод, полученный исполнением на предмете. Ожидание,
# написанное из головы («ожидаемый вывод — 1»), сюда намеренно не попадает.
DEFAULT_EXECUTED = [
    r"фактическ\w*\s+(вывод|выдач\w*)",
    r"(вывод|выдач\w*)\s+на\s+предмете",
    r"исполнен\w*\s+на\s+(текущем\s+)?предмете",
    r"прогнан\w*\s+на\s+предмете",
    r"вывод\s+прогона",
]
DEFAULT_NOT_EXECUTED = [r"не\s+исполнял\w*"]
DEFAULT_EXPECTATION = [
    r"ожидаемо\s*[:—–-]",
    r"ожидаем\w*\s+(вывод|выдач\w*)\s*[:—–-]",
    r"ожидани[ея]\s*[:—–-]",
    r"ожидаетс[яь]\s*[:—–-]",
]


def _compile(patterns) -> list[re.Pattern]:
    return [re.compile(p, re.IGNORECASE) for p in patterns]


def _lead(lines: list[str], fence_line: int, lo: int) -> list[str]:
    """Абзац-зачин блока: ближайший абзац над оградой, не выше границы lo.

    Пустая строка между поручением и его блоком абзаца не разрывает: перечень
    §7 пишется как «строка-поручение, под ней блок», и пометка «не исполнялась»
    стоит именно в этой строке. Дальше одного абзаца поиск не идёт — ожидание
    предыдущего пункта чужой блок не оправдывает.
    """
    out: list[str] = []
    ln = fence_line - 1
    while ln >= lo and not lines[ln - 1].strip():      # один пробел-разделитель
        ln -= 1
    while ln >= lo and lines[ln - 1].strip():
        raw = lines[ln - 1]
        if FENCE.match(raw) or MD_HEADING.match(raw):
            break
        out.append(raw)
        ln -= 1
    return list(reversed(out))


def _tail(lines: list[str], after: int, hi: int, paragraphs: int) -> list[str]:
    """Строки после блока: тот же абзац и следующие, всего `paragraphs` абзацев."""
    out: list[str] = []
    done, open_par = 0, False
    ln = after + 1
    while ln <= hi and done < paragraphs:
        raw = lines[ln - 1]
        if not raw.strip():
            if open_par:
                done += 1
                open_par = False
            ln += 1
            continue
        if FENCE.match(raw) or MD_HEADING.match(raw):
            break
        open_par = True
        out.append(raw)
        ln += 1
    return out


def _expectation(region: list[str], patterns: list[re.Pattern],
                 min_chars: int) -> str | None:
    """Названное ожидание: хвост после маркера не короче min_chars значащих."""
    for idx, line in enumerate(region):
        for pat in patterns:
            m = pat.search(line)
            if not m:
                continue
            tail = line[m.end():].strip()
            # продолжение на следующей строке засчитывается, если она не пуста
            if significant_chars(tail) < min_chars and idx + 1 < len(region):
                tail = (tail + " " + region[idx + 1].strip()).strip()
            if significant_chars(tail) >= min_chars:
                return m.group(0).strip()
    return None


def check(text: str, config: dict) -> list[Finding]:
    config = config or {}
    head_re = re.compile(config.get("section_pattern", DEFAULT_SECTION), re.IGNORECASE)
    end_re = re.compile(config.get("section_end_pattern", DEFAULT_SECTION_END),
                        re.IGNORECASE)
    max_lines = int(config.get("section_max_lines", DEFAULT_SECTION_MAX_LINES))
    paragraphs = int(config.get("expectation_paragraphs",
                                DEFAULT_EXPECTATION_PARAGRAPHS))
    executed = _compile(config.get("executed_patterns") or DEFAULT_EXECUTED)
    not_executed = _compile(config.get("not_executed_patterns") or DEFAULT_NOT_EXECUTED)
    expectation = _compile(config.get("expectation_patterns") or DEFAULT_EXPECTATION)
    min_chars = int(config.get("min_expectation_chars", 10))

    lines = split_lines(text)
    blocks = parse_blocks(text, config)
    findings: list[Finding] = []

    for lo, hi in head_sections(lines, head_re, end_re, max_lines):
        for b in blocks:
            if not (lo <= b.fence_line <= hi):
                continue
            after = b.end + 1 if b.closed else b.end
            region = _lead(lines, b.fence_line, lo) + _tail(lines, after, hi, paragraphs)
            said = _expectation(region, expectation, min_chars)
            if said is None:
                findings.append(Finding(
                    b.fence_line, NAME, RED,
                    "исполняемый блок выдан владельцу без строки ожидания его "
                    "вывода: ни в том же абзаце, ни в следующем нет «Ожидаемо: …» "
                    f"с ожиданием не короче {min_chars} значащих символов — "
                    "владелец получает команду и не получает, с чем сравнить "
                    "её вывод (§7: пункт называет, какой результат считается "
                    "ожидаемым)"))
                continue
            blob = "\n".join(region)
            if any(p.search(blob) for p in executed):
                continue
            if any(p.search(blob) for p in not_executed):
                continue
            findings.append(Finding(
                b.fence_line, NAME, RED,
                f"ожидание вывода блока («{said}») не цитирует фактический вывод "
                "на предмете и не помечено «не исполнялась — первый прогон у "
                "владельца»: ожидание из головы неотличимо от измеренного — "
                "перед выдачей блок исполняется на предмете, и ожидаемый вывод "
                "берётся из этого исполнения"))
    return findings
