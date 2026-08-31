"""S-06 / К2: строка смоука без цитаты фактического вывода и без пометки.

Опора: контракт §11, «Строка смоука — детектор и исполняется на предмете до
выдачи» (реестр: R-SMOKE-004). Дословная проверка пункта: handoff со строкой
смоука либо цитирует её фактический вывод на предмете, либо несёт пометку
«не исполнялась» с названным ожиданием; строка без того и другого возвращается.

Область строки смоука — от её маркера до ближайшей границы: следующего маркера,
markdown-заголовка либо конца окна (`scope_lines`). Внутри области ищется одно
из двух: цитата фактического вывода (`executed_patterns`) либо пометка
«не исполнялась» (`not_executed_patterns`) вместе с названным ожиданием
(`expectation_patterns` и `min_expectation_chars` значащих символов после
двоеточия). Пометка без ожидания — тоже красный: владелец получает строку, но
не получает, с чем сравнить её вывод.

Списки форм — данные, а не код: они живут в linter/manifest.yaml и расширяются
без правки этого модуля.

Чистая функция: ни сети, ни LLM, ни файловых эффектов.
"""

from __future__ import annotations

import re

from ..common import RED, Finding, significant_chars, split_lines

NAME = "smoke_line"

MD_HEADING = re.compile(r"^#{1,6}\s")

DEFAULT_MARKERS = [
    r"строк\w*\s+смоука",
    r"смоук-строк\w*",
    r"проверочн\w*\s+строк\w*",
]
# «Фактический вывод» — вывод, полученный исполнением на предмете. Ожидание,
# написанное из головы («ожидаемый вывод — 1»), сюда намеренно не попадает:
# именно его неотличимость от измеренного пункт канона и запрещает.
DEFAULT_EXECUTED = [
    r"фактическ\w*\s+(вывод|выдач\w*)",
    r"(вывод|выдач\w*)\s+на\s+предмете",
    r"исполнен\w*\s+на\s+(текущем\s+)?предмете",
    r"прогнан\w*\s+на\s+предмете",
    r"вывод\s+прогона",
]
DEFAULT_NOT_EXECUTED = [r"не\s+исполнял\w*"]
DEFAULT_EXPECTATION = [
    r"ожидани[ея]\s*[:—–-]",
    r"ожидаетс[яь]\s*[:—–-]",
    r"ожидаем\w*\s+(вывод|выдач\w*)\s*[:—–-]",
]


def _compile(patterns) -> list[re.Pattern]:
    return [re.compile(p, re.IGNORECASE) for p in patterns]


def _expectation_named(region: list[str], patterns: list[re.Pattern],
                       min_chars: int) -> bool:
    """Названо ли ожидание: хвост после двоеточия не короче min_chars значащих."""
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
                return True
    return False


def check(text: str, config: dict) -> list[Finding]:
    config = config or {}
    markers = _compile(config.get("smoke_markers") or DEFAULT_MARKERS)
    executed = _compile(config.get("executed_patterns") or DEFAULT_EXECUTED)
    not_executed = _compile(config.get("not_executed_patterns") or DEFAULT_NOT_EXECUTED)
    expectation = _compile(config.get("expectation_patterns") or DEFAULT_EXPECTATION)
    scope = int(config.get("scope_lines", 12))
    min_chars = int(config.get("min_expectation_chars", 10))

    lines = split_lines(text)
    hits = [i for i, line in enumerate(lines)
            if any(m.search(line) for m in markers)]

    findings: list[Finding] = []
    for pos, i in enumerate(hits):
        end = min(len(lines), i + 1 + scope)
        if pos + 1 < len(hits):
            end = min(end, hits[pos + 1])
        for j in range(i + 1, end):
            if MD_HEADING.match(lines[j]):
                end = j
                break
        region = lines[i:end]
        blob = "\n".join(region)

        if any(p.search(blob) for p in executed):
            continue
        if any(p.search(blob) for p in not_executed):
            if _expectation_named(region, expectation, min_chars):
                continue
            findings.append(Finding(
                i + 1, NAME, RED,
                "строка смоука помечена «не исполнялась», но ожидание не названо: "
                f"владелец получает строку и не получает, с чем сравнить её вывод "
                f"(нужна строка «Ожидание: …» не короче {min_chars} значащих символов)"))
            continue
        findings.append(Finding(
            i + 1, NAME, RED,
            "строка смоука не цитирует фактический вывод на предмете и не несёт "
            "пометки «не исполнялась — первый прогон у владельца» с названным "
            "ожиданием: ожидание из головы неотличимо от измеренного, "
            "строка возвращается на исполнение"))
    return findings
