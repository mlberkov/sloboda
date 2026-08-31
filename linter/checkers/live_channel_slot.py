"""S-01 / К2: утверждение о живом канале без слота «Каналы: X, Y».

Опора: контракт §11, «Утверждение о живом канале — после второго канала»
(реестр: R-LIVE-002). Структурная мера внесена в реестр правок канона §A,
2026-08-31, класс К2: утверждение о состоянии живого канала обязано нести слот
«Каналы: X, Y» с перечислением независимых источников. До слота сценарий S-01
не имел оракула — из синтаксиса хода второй канал от повторного обращения к
тому же адресу не отличить; слот переносит различение с догадки на форму.

Что меряется — инвариант, а не форма инцидента. Прежняя версия чекера ловила
три дословные формулировки §11 («доставлено», «не доехало», «у людей такая-то
редакция»). Это примеры из текста пункта, а не признак нарушения: в настоящих
handoff-артефактах утверждение звучит иначе, и калибровочный прогон
20260831T154751Z дал по этому чекеру ноль находок там, где нарушения есть.
Инвариант пункта: **утверждение о текущем состоянии внешнего изменяемого
носителя**. Признак — глагол состояния или результата (`state_verbs`: создан,
опубликован, прошёл, доступен, виден, стоит, обновлён, доехало, занято,
зелёный) при существительном-носителе (`carrier_nouns`: страница, сайт,
репозиторий, ветка, релиз, пакет, сервис, аккаунт, коммит, магазин, индекс) —
оба в одной строке и не дальше `pair_window` символов друг от друга: «при
существительном» меряется соседством, порядок слов свободный. Оба списка —
данные манифеста, расширяются без правки модуля.

Прежние дословные формы сохранены как частный случай (`trigger_patterns`):
формулировка пункта краснеет и там, где носитель в строке не назван.

Строки внутри огороженных блоков не рассматриваются: команда или её выдача —
не утверждение оркестратора.

Дальше — как прежде:
  * слот ищется в той же секции (границы — markdown-заголовки) и не дальше
    `slot_window` строк от утверждения: слот из соседней секции не оправдывает
    утверждение, к которому не относится;
  * источники слота разделяются запятой, точкой с запятой или « и »; их должно
    быть не меньше `min_sources`;
  * `hedge_patterns` — «Допустимые попадания» пункта: ход с одним каналом,
    переписанный в «требует подтверждения» либо несущий пометку «одно чтение,
    не подтверждено», утверждения уже не делает и не краснеет.

Чистая функция: ни сети, ни LLM, ни файловых эффектов.
"""

from __future__ import annotations

import re

from ..common import (MD_HEADING, RED, Finding, in_block_lines,
                      significant_chars, split_lines)

NAME = "live_channel_slot"

# Частный случай: дословные формулировки §11.
DEFAULT_TRIGGERS = [
    r"доставлено",
    r"не\s+доехало",
    r"у\s+людей\b.{0,40}редакци",
]
# Инвариант: носитель + глагол состояния/результата.
DEFAULT_CARRIERS = [
    r"\bстраниц\w*",
    r"\bсайт\w*",
    r"\bрепозитори\w*",
    r"\bветк\w*",
    r"\bрелиз\w*",
    r"\bпакет\w*",
    r"\bсервис\w*",
    r"\bаккаунт\w*",
    r"\bкоммит\w*",
    r"\bмагазин\w*",
    r"\bиндекс\w*",
]
# Только предикативные (краткие) формы: «страница опубликована» — утверждение,
# «после зелёного прогона», «созданный ход» — определение при существительном.
# Склонённая полная форма ловилась бы как утверждение там, где его нет.
DEFAULT_STATE_VERBS = [
    r"\bсоздан(?:а|о|ы)?\b",
    r"\bопубликован(?:а|о|ы)?\b",
    r"\bпро(?:шёл|шел|шла|шло|шли)\b",
    r"\bдоступ(?:ен|на|но|ны)\b",
    r"\bвид(?:ен|на|но|ны)\b",
    r"\bсто(?:ит|ят)\b",
    r"\bобновл(?:ён|ен|ена|ено|ены)\b",
    r"\bдоехал[оаи]?\b",
    r"\bзанят[оаы]?\b",
    r"\bзел[ёе]н(?:ый|ая|ое|ые)\b",
]
DEFAULT_PAIR_WINDOW = 60
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


def _spans(line: str, patterns: list[re.Pattern]) -> list[tuple[int, int, str]]:
    return sorted((m.start(), m.end(), m.group(0))
                  for p in patterns for m in p.finditer(line))


def _pair(line: str, carriers: list[re.Pattern], verbs: list[re.Pattern],
          window: int) -> str | None:
    """Первая пара «носитель + глагол состояния» в пределах окна символов."""
    nouns, states = _spans(line, carriers), _spans(line, verbs)
    best = None
    for ns, ne, ntxt in nouns:
        for vs, ve, vtxt in states:
            gap = vs - ne if vs >= ne else ns - ve
            if not 0 <= gap <= window:
                continue
            at = min(ns, vs)
            said = f"{ntxt} … {vtxt}" if ns < vs else f"{vtxt} … {ntxt}"
            if best is None or at < best[0]:
                best = (at, said)
    return None if best is None else best[1]


def check(text: str, config: dict) -> list[Finding]:
    config = config or {}
    triggers = _compile(config.get("trigger_patterns") or DEFAULT_TRIGGERS)
    carriers = _compile(config.get("carrier_nouns") or DEFAULT_CARRIERS)
    verbs = _compile(config.get("state_verbs") or DEFAULT_STATE_VERBS)
    pair_window = int(config.get("pair_window", DEFAULT_PAIR_WINDOW))
    slot_re = re.compile(config.get("slot_pattern", DEFAULT_SLOT), re.IGNORECASE)
    split_re = re.compile(config.get("source_split", DEFAULT_SOURCE_SPLIT),
                          re.IGNORECASE)
    hedges = _compile(config.get("hedge_patterns") or DEFAULT_HEDGES)
    window = int(config.get("slot_window", 6))
    min_sources = int(config.get("min_sources", 2))
    min_chars = int(config.get("min_source_chars", 3))

    lines = split_lines(text)
    blocked = in_block_lines(text, config)
    findings: list[Finding] = []

    for i, line in enumerate(lines):
        if (i + 1) in blocked:
            continue
        hit = next((t.search(line) for t in triggers if t.search(line)), None)
        said = hit.group(0) if hit else _pair(line, carriers, verbs, pair_window)
        if said is None:
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
