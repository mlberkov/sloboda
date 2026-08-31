"""Маркеры изъятия находок.

Форма маркера — строка-комментарий markdown:

    <!-- lint:ignore <checker> — причина -->

Маркер действует ровно на **следующую** строку артефакта и только на названный
чекер: изъятие адресное, «выключить линтер до конца файла» этой формой нельзя.

Причина обязательна. Изъятие с пустой причиной не действует — находка чекера
остаётся, — и само даёт находку `checker=ignore_without_reason`,
`severity=error`: молчаливое изъятие неотличимо от отсутствия предмета,
а именно эту неотличимость полоса и ловит.

Чистая функция: ни сети, ни файловых эффектов.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .common import ERROR, Finding, split_lines

NAME = "ignore_without_reason"

# Тире причины — em/en-dash либо дефис(ы); пробелы вокруг свободные.
MARKER = re.compile(
    r"<!--\s*lint:ignore\s+(?P<checker>[A-Za-z_][A-Za-z0-9_]*)\s*"
    r"(?:(?:[—–]|-{1,2})\s*(?P<reason>.*?))?\s*-->"
)


@dataclass(frozen=True)
class Ignore:
    line: int        # 1-индексная строка самого маркера
    target: int      # строка, на которую маркер действует (line + 1)
    checker: str
    reason: str
    valid: bool      # причина непуста


def parse(text: str) -> list[Ignore]:
    """Все маркеры изъятия артефакта в порядке появления."""
    out: list[Ignore] = []
    for idx, raw in enumerate(split_lines(text), start=1):
        for m in MARKER.finditer(raw):
            reason = (m.group("reason") or "").strip()
            out.append(Ignore(line=idx, target=idx + 1, checker=m.group("checker"),
                              reason=reason, valid=bool(reason)))
    return out


def apply(text: str, findings: list[Finding]) -> tuple[list[Finding], int]:
    """Изымает находки по маркерам.

    Возвращает (оставшиеся находки + находки о безпричинных изъятиях,
    число применённых изъятий). «Применённое» — то, которое действительно
    сняло находку: маркер, не совпавший ни с одной находкой, не считается.
    """
    valid: set[tuple[int, str]] = set()
    extra: list[Finding] = []
    for ig in parse(text):
        if ig.valid:
            valid.add((ig.target, ig.checker))
        else:
            extra.append(Finding(
                ig.line, NAME, ERROR,
                f"изъятие без причины: `<!-- lint:ignore {ig.checker} — причина -->` "
                f"требует непустой причины — находка чекера {ig.checker} "
                f"на строке {ig.target} не изымается"))

    kept: list[Finding] = []
    applied = 0
    for f in findings:
        if (f.line, f.checker) in valid:
            applied += 1
            continue
        kept.append(f)
    return kept + extra, applied
