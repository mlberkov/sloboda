"""S-01 / К2: утверждение о живом канале без слота «Каналы: X, Y».

Опора: контракт §11, «Утверждение о живом канале — после второго канала»
(реестр: R-LIVE-002). Структурная мера внесена в реестр правок канона §A,
2026-08-31, класс К2: утверждение о состоянии живого канала обязано нести слот
«Каналы: X, Y» с перечислением независимых источников. До слота сценарий S-01
не имел оракула — из синтаксиса хода второй канал от повторного обращения к
тому же адресу не отличить; слот переносит различение с догадки на форму.

Что меряется:
  * `trigger_patterns` — формулировки утверждения о живом канале. Стартовый
    набор взят дословно из §11 («доставлено», «не доехало», «у людей такая-то
    редакция»); список — данные в linter/manifest.yaml, расширяется без правки
    модуля.
  * слот ищется в той же секции (границы — markdown-заголовки) и не дальше
    `slot_window` строк от утверждения: слот из соседней секции не оправдывает
    утверждение, к которому не относится.
  * источники слота разделяются запятой, точкой с запятой или « и »; их должно
    быть не меньше `min_sources`.
  * `hedge_patterns` — «Допустимые попадания» пункта: ход с одним каналом,
    переписанный в «требует подтверждения» либо несущий пометку «одно чтение,
    не подтверждено», утверждения уже не делает и не краснеет.

Чистая функция: ни сети, ни LLM, ни файловых эффектов.
"""

from __future__ import annotations

import re

from ..common import RED, Finding, significant_chars, split_lines

NAME = "live_channel_slot"

MD_HEADING = re.compile(r"^#{1,6}\s")

# Стартовый набор — дословные формулировки §11, не сочинённые.
DEFAULT_TRIGGERS = [
    r"доставлено",
    r"не\s+доехало",
    r"у\s+людей\b.{0,40}редакци",
]
DEFAULT_SLOT = r"^[\s*_>#|-]*Каналы\s*\**\s*[:—–-]\s*(.+)$"
DEFAULT_SOURCE_SPLIT = r",|;|\sи\s"
DEFAULT_HEDGES = [
    r"одно\s+чтение,?\s*не\s+подтверждено",
    r"требует\s+подтверждения",
]


def _compile(patterns) -> list[re.Pattern]:
    return [re.compile(p, re.IGNORECASE) for p in patterns]


def _section(lines: list[str], i: int) -> tuple[int, int]:
    """Границы секции строки i: [начало, конец) между markdown-заголовками."""
    start = 0
    for j in range(i, -1, -1):
        if MD_HEADING.match(lines[j]):
            start = j
            break
    end = len(lines)
    for j in range(i + 1, len(lines)):
        if MD_HEADING.match(lines[j]):
            end = j
            break
    return start, end


def _sources(tail: str, split_re: re.Pattern, min_chars: int) -> list[str]:
    """Источники слота: элементы разделённого хвоста, несущие содержание."""
    tail = tail.strip().rstrip("*").strip().rstrip(".").strip()
    return [p.strip() for p in split_re.split(tail)
            if significant_chars(p) >= min_chars]


def check(text: str, config: dict) -> list[Finding]:
    config = config or {}
    triggers = _compile(config.get("trigger_patterns") or DEFAULT_TRIGGERS)
    slot_re = re.compile(config.get("slot_pattern", DEFAULT_SLOT), re.IGNORECASE)
    split_re = re.compile(config.get("source_split", DEFAULT_SOURCE_SPLIT),
                          re.IGNORECASE)
    hedges = _compile(config.get("hedge_patterns") or DEFAULT_HEDGES)
    window = int(config.get("slot_window", 6))
    min_sources = int(config.get("min_sources", 2))
    min_chars = int(config.get("min_source_chars", 3))

    lines = split_lines(text)
    findings: list[Finding] = []

    for i, line in enumerate(lines):
        hit = next((t for t in triggers if t.search(line)), None)
        if hit is None:
            continue
        # Слот, стоящий на самой строке утверждения, не оправдывает её дважды:
        # он и есть слот этой строки — область поиска включает саму строку.
        sec_start, sec_end = _section(lines, i)
        lo = max(sec_start, i - window)
        hi = min(sec_end, i + window + 1)
        region = lines[lo:hi]
        blob = "\n".join(region)

        if any(h.search(blob) for h in hedges):
            continue

        said = hit.search(line).group(0)
        slot = next((m for m in (slot_re.match(r) for r in region) if m), None)
        if slot is None:
            findings.append(Finding(
                i + 1, NAME, RED,
                f"утверждение о состоянии живого канала («{said}») без слота "
                f"«Каналы: X, Y»: независимость второго канала из текста хода "
                f"не выводится — повторное обращение к тому же адресу читается "
                f"как второй канал"))
            continue
        sources = _sources(slot.group(1), split_re, min_chars)
        if len(sources) < min_sources:
            findings.append(Finding(
                i + 1, NAME, RED,
                f"утверждение о состоянии живого канала («{said}») со слотом на "
                f"{len(sources)} источник(ах) «{', '.join(sources) or '—'}»: пункт требует "
                f"не менее {min_sources} независимых каналов и обеих выдач — иначе кэш "
                f"канала чтения выдаёт устаревший предмет при соблюдённом провенансе"))
    return findings
