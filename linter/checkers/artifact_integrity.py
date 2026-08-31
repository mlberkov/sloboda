"""S-09 / К2: handoff, потерявший ограды, — деградировавший вход, а не чистый ход.

Опора: контракт §11, «Пустая выдача при нулевом коде выхода — отказ канала»
(добавлено 2026-08-22, разбор закрытия L3; реестр: R-VACUUM-007). Пункт говорит
о выдаче инструмента чтения: ноль байт при `exit 0` не является отрицательным
результатом — сверка объявляется **не выполненной**, вердикт по ней не строится.
Здесь это правило применено к самому линтеру: линтер — тоже инструмент чтения,
и его «ноль находок» на артефакте, где мерить нечего, ровно та же пустая выдача
при нулевом коде выхода.

Что меряется. Артефакт вида handoff, в котором **ноль огороженных блоков**, но
при этом видно, что ход поручает владельцу действия: есть раздел «Ваши
действия» либо в тексте не меньше `min_verbs` глаголов исполнения из словаря
owner_action_block (`action_verbs`, общие данные манифеста — `shared`). Такой
вход деградировал: ограды потерялись по дороге (обрезка канала, пересборка
разметки, копирование в чат без блоков), а не «блоков не было».

Почему это infra, а не вердикт о предмете. Пять чекеров полосы — smoke_line,
stop_provenance, shell_mech, env_presupposition, grep_vacuum — опираются на
огороженный блок: без ограды у них нет предмета. На деградировавшем входе они
дали бы ноль находок, и этот ноль неотличим от «нарушений нет». Отказала опора
прогона, а не артефакт под чекером, — поэтому класс infra, а перечисленные
чекеры на таком входе **не запускаются вовсе** и в отчёте помечаются
«не измерено», а не «ноль находок». Кто именно гасится — данные (`gates` в
linter/manifest.yaml); гашение исполняет run.py, читая тот же список.

Допустимые попадания пункта («каналы, где пустота — документированный законный
ответ») здесь — ход без действий владельца: он не несёт ни раздела, ни двух
глаголов исполнения, и отсутствие блоков в нём законно.

Чистая функция: ни сети, ни LLM, ни файловых эффектов.
"""

from __future__ import annotations

import re

from ..common import RED, Finding, parse_blocks, split_lines

NAME = "artifact_integrity"

# Итог контроля. Он же — статус строки infra в отчёте прогона.
STATUS = "artifact_degraded"

DEFAULT_SECTION = r"^[\s*_>#|-]*\**\s*Ваши\s+действия\b"
DEFAULT_MIN_VERBS = 2

# Умолчание повторяет словарь owner_action_block: в манифесте оба чекера читают
# один список из `shared`, здесь — страховка на случай запуска без манифеста.
DEFAULT_VERBS = [
    r"\bпрогн(?:ать|ал|али)\b",
    r"\bпрогони(?:те)?\b",
    r"\bзакоммит(?:ить|ь|ьте|ил|или)\b",
    r"\bвыполн(?:ить|и|ите|ил|или|ять|яйте)\b",
    r"\bзапуст(?:ить|и|ите|ил|или)\b",
]

DEFAULT_GATES = ["smoke_line", "stop_provenance", "shell_mech",
                 "env_presupposition", "grep_vacuum"]


def gated(config: dict) -> list[str]:
    """Чекеры, которые на деградировавшем входе мерить нечем."""
    return list((config or {}).get("gates") or DEFAULT_GATES)


def check(text: str, config: dict) -> list[Finding]:
    config = config or {}
    head_re = re.compile(config.get("section_pattern", DEFAULT_SECTION), re.IGNORECASE)
    min_verbs = int(config.get("min_verbs", DEFAULT_MIN_VERBS))
    verbs = [re.compile(p, re.IGNORECASE)
             for p in (config.get("action_verbs") or DEFAULT_VERBS)]

    if parse_blocks(text, config):
        return []                        # ограды на месте — мерить есть что

    lines = split_lines(text)
    section_line = next((i for i, raw in enumerate(lines, start=1)
                         if head_re.match(raw)), None)

    hits: list[tuple[int, str]] = []
    for i, raw in enumerate(lines, start=1):
        for v in verbs:
            m = v.search(raw)
            if m:
                hits.append((i, m.group(0)))

    if section_line is None and len(hits) < min_verbs:
        return []                        # ход без действий владельца: блоков и не ждём

    if section_line is not None:
        line = section_line
        trigger = "есть раздел «Ваши действия»"
    else:
        line = hits[0][0]
        shown = ", ".join(f"«{w}»" for _, w in hits[:3])
        trigger = (f"в тексте {len(hits)} глагол(ов) исполнения из словаря "
                   f"owner_action_block ({shown}) при пороге {min_verbs}")

    lost = (f"ограды утеряны: чекеры, опирающиеся на блоки "
            f"({', '.join(gated(config))}), измерены быть не могут")

    return [Finding(
        line, NAME, RED,
        f"деградировавший вход: в handoff-артефакте ноль огороженных блоков, "
        f"но {trigger} — {lost}. Их ноль находок был бы пустой выдачей при "
        f"нулевом коде выхода, а она по §11 не отрицательный результат: "
        f"сверка объявляется не выполненной, вердикт по ней не строится. "
        f"Владельцу — добыть артефакт другим путём (исходная выдача хода, "
        f"файл без пересборки разметки) и повторить прогон")]
