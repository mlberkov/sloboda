"""S-04 / К2: поиск, чей нулевой результат неотличим от отсутствия предмета.

Опора: контракт §11, «Строка смоука — детектор и исполняется на предмете до
выдачи» + «Исполнимость shell-блока» (3) «Отличимость провала», (3а) «Полнота
отрицания», (3б) «Отрицание вне shell-блока».
Реестр правок канона §B: рецидив 2026-08-28 — шаблон грепа по невиданной разметке.
"""

from __future__ import annotations

import re

from ..common import RED, Finding, shell_blocks

NAME = "grep_vacuum"

DEFAULT_SEARCH = r"(?<![\w-])(?:git\s+)?(?:grep|rg|ugrep)\b"
DEFAULT_SAME_LINE = (
    r"(\|\|\s*(echo|printf)|"          # явный маркер отрицания
    r"(?:grep|ugrep)\s+(-\w*c\w*|--count)\b|"
    r"\brg\s+(-\w*c\w*|--count|--count-matches)\b|"
    r"-c\b(?=[^|]*$))"
)
DEFAULT_IN_BLOCK = (
    r"(test\s+-s\b|\[\s+-s\s|\[\[\s+-s\s|"   # вход измерен на непустоту
    r"\bwc\s+-l\b|--count-matches\b)"
)


def check(text: str, config: dict) -> list[Finding]:
    config = config or {}
    search = re.compile(config.get("search_pattern", DEFAULT_SEARCH))
    same_line = re.compile(config.get("absolved_same_line", DEFAULT_SAME_LINE))
    in_block = re.compile(config.get("absolved_in_block", DEFAULT_IN_BLOCK))
    lookahead = int(config.get("marker_lookahead", 1))

    findings: list[Finding] = []
    for b in shell_blocks(text, config):
        numbered = list(b.numbered())
        block_text = "\n".join(b.lines)
        block_absolved = bool(in_block.search(block_text))
        for pos, (ln, raw) in enumerate(numbered):
            if not search.search(raw):
                continue
            window = "\n".join(r for _, r in numbered[pos:pos + 1 + lookahead])
            if same_line.search(window):
                continue
            if block_absolved:
                continue
            findings.append(Finding(
                ln, NAME, RED,
                "поиск без анти-вакуумной ноги: нулевая выдача неотличима от "
                "отсутствия предмета, несовпавшего шаблона и пустого входа — "
                "нужен `|| echo`-маркер отрицания либо счёт попаданий "
                "на измеренно непустом входе"))
    return findings
