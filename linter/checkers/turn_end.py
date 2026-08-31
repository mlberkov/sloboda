"""S-05 / К4: конец хода не называет, какое слово владельца требуется.

Опора: контракт §11, «Конец хода — точка, где нужно слово владельца».
Структурная половина сценария: наличие строки и непустота требования после
двоеточия. Существо (нужно ли здесь вообще слово владельца) — manual.
"""

from __future__ import annotations

import re

from ..common import RED, Finding, significant_chars, split_lines

NAME = "turn_end"

DEFAULT_MARKER = r"^\s*(?:[*_#>\-\s]*)\**\s*Конец хода\**\s*(?::|—|-)?\s*(.*)$"


def check(text: str, config: dict) -> list[Finding]:
    config = config or {}
    marker = re.compile(config.get("marker_pattern", DEFAULT_MARKER),
                        re.IGNORECASE | re.MULTILINE)
    min_sig = int(config.get("min_significant_chars", 10))
    require_presence = bool(config.get("require_presence", True))

    lines = split_lines(text)
    hits = []
    for idx, raw in enumerate(lines):
        m = marker.match(raw)
        if m:
            hits.append((idx + 1, m.group(1).strip()))

    if not hits:
        if not require_presence:
            return []
        last = max(1, len([l for l in lines if l.strip()]) and len(lines))
        return [Finding(
            last, NAME, RED,
            "в handoff-артефакте нет строки «Конец хода»: точка, где нужно слово "
            "владельца, не названа — граница хода не проверяема")]

    findings: list[Finding] = []
    for ln, tail in hits:
        # продолжение на следующей строке засчитывается, если строка не пуста
        if significant_chars(tail) < min_sig and ln < len(lines):
            tail = (tail + " " + lines[ln].strip()).strip()
        if significant_chars(tail) < min_sig:
            findings.append(Finding(
                ln, NAME, RED,
                f"«Конец хода» не называет требуемое слово владельца: после "
                f"двоеточия {significant_chars(tail)} значащих символов "
                f"(порог {min_sig})"))
    return findings
