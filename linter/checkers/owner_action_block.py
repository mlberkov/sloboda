"""S-08 / К4: пункт «Ваших действий» с поручением исполнить команду — без блока.

Опора: контракт §7, «Действия владельца — отдельным блоком, а не прозой»
(добавлено 2026-08-29; реестр: R-OWNERBLOCK-006). Пункт требует, чтобы все
действия владельца хода стояли в одном озаглавленном перечне «Ваши действия»,
по строке на действие, а копируемый блок был **пунктом этого перечня, а не
заменой ему**; «проза не содержит поручений». Ловимый отказ назван там же:
владелец исполнил копируемый блок и счёл ход закрытым, тогда как поручение
стояло прозой рядом.

Что меряется. В разделе «Ваши действия» пункт, чей текст несёт глагол
исполнения команды (`action_verbs`), обязан нести огороженный блок — в самом
пункте либо в следующем: перечень часто пишется как «строка-поручение, под ней
блок», и блок в этом случае оказывается отдельным абзацем-пунктом.

Границы. Раздел — от строки заголовка до ближайшего из: markdown-заголовка,
строки «Конец хода» (`section_end_pattern`), следующего заголовка «Ваши
действия», `section_max_lines` строк. Пункт — элемент списка (нумерованный или
маркированный) либо абзац: непустая строка вне ограды, перед которой пустая
строка или ограда. Строки внутри оград пунктов не начинают — блок принадлежит
пункту, в котором стоит.

Списки глаголов и шаблоны границ — данные: живут в linter/manifest.yaml и
расширяются без правки этого модуля.

Чистая функция: ни сети, ни LLM, ни файловых эффектов.
"""

from __future__ import annotations

import re

from ..common import (RED, Finding, head_sections, in_block_lines,
                      parse_blocks, split_lines)

NAME = "owner_action_block"

DEFAULT_SECTION = r"^[\s*_>#|-]*\**\s*Ваши\s+действия\b"
DEFAULT_SECTION_END = r"^[\s*_>#|-]*\**\s*Конец\s+хода\b"
DEFAULT_ITEM = r"^\s*(?:\d+[.)]|[-*+•])\s+"
DEFAULT_SECTION_MAX_LINES = 40

# Стартовый набор — глаголы, названные владельцем при заведении чекера
# (2026-08-31): «прогнать», «закоммитить», «выполнить», «запустить».
# Только личные формы и инфинитив: отглагольное существительное («вывод
# прогона») поручением не является и краснеть не должно.
DEFAULT_VERBS = [
    r"\bпрогн(?:ать|ал|али)\b",
    r"\bпрогони(?:те)?\b",
    r"\bзакоммит(?:ить|ь|ьте|ил|или)\b",
    r"\bвыполн(?:ить|и|ите|ил|или|ять|яйте)\b",
    r"\bзапуст(?:ить|и|ите|ил|или)\b",
]


def _items(lines: list[str], lo: int, hi: int, blocked: set[int], item_re):
    """Пункты раздела: списки 1-индексных номеров строк [начало, конец]."""
    starts: list[int] = []
    for ln in range(lo, hi + 1):
        raw = lines[ln - 1]
        if ln in blocked or not raw.strip():
            continue
        if item_re.match(raw):
            starts.append(ln)
            continue
        prev = lines[ln - 2] if ln >= 2 else ""
        if ln == lo or not prev.strip() or (ln - 1) in blocked:
            starts.append(ln)
    spans = []
    for k, s in enumerate(starts):
        e = starts[k + 1] - 1 if k + 1 < len(starts) else hi
        spans.append((s, e))
    return spans


def check(text: str, config: dict) -> list[Finding]:
    config = config or {}
    head_re = re.compile(config.get("section_pattern", DEFAULT_SECTION), re.IGNORECASE)
    end_re = re.compile(config.get("section_end_pattern", DEFAULT_SECTION_END),
                        re.IGNORECASE)
    item_re = re.compile(config.get("item_pattern", DEFAULT_ITEM))
    max_lines = int(config.get("section_max_lines", DEFAULT_SECTION_MAX_LINES))
    verbs = [re.compile(p, re.IGNORECASE)
             for p in (config.get("action_verbs") or DEFAULT_VERBS)]

    lines = split_lines(text)
    blocked = in_block_lines(text, config)
    fences = [b.fence_line for b in parse_blocks(text, config)]
    findings: list[Finding] = []

    for lo, hi in head_sections(lines, head_re, end_re, max_lines):
        spans = _items(lines, lo, hi, blocked, item_re)
        has_block = [any(s <= f <= e for f in fences) for s, e in spans]
        for k, (s, e) in enumerate(spans):
            body = " ".join(lines[ln - 1] for ln in range(s, e + 1)
                            if ln not in blocked)
            hit = next((v.search(body) for v in verbs if v.search(body)), None)
            if hit is None:
                continue
            if has_block[k] or (k + 1 < len(spans) and has_block[k + 1]):
                continue
            findings.append(Finding(
                s, NAME, RED,
                f"пункт «Ваших действий» поручает исполнить команду "
                f"(«{hit.group(0)}»), но огороженного блока нет ни в нём, ни в "
                f"следующем пункте: поручение прозой владелец собирает по памяти "
                f"либо пропускает, исполнив копируемый блок выше и сочтя ход "
                f"закрытым — §7 требует блок, а не прозу"))
    return findings
