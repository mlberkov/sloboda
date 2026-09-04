"""S-22 / К6: копируемый промпт кодирует решения вольта без Sources-трейлера.

Опора: prompt-kit, раздел «Правила использования», пункт «Sources-трейлер»
(добавлено 2026-07-28, У1; реестр: R-SOURCES-020). Пункт: packet-handoff'ы несут
заполненный оркестратором блок `Sources` — список vault-якорей (ADR/PDR/спека
плюс одна строка «что ограничивает»), которые handoff кодирует; **агент вольт не
читает**, и трейлер — единственная трасса vault → handoff → план. Второй
читатель трейлера назван там же: plan review контракта §8 сверяет «Decisions &
assumptions» плана против этого списка. Без списка сверять не с чем: план
проходит ревью против пустого множества и выглядит согласованным.

Что меряется — инвариант, а не форма инцидента (реестр правок канона §D,
2026-08-31, третий случай). Прежний триггер искал «упоминание ADR-» в блоке
длиннее 15 строк: handoff, кодирующий решение вольта словами «реестр §D»,
«несущий документ §6», «контракт §11», «owner decision 2=а», проходил мимо.

Инвариант складывается из трёх частей:
  * блок — копируемый промпт (признак общий с `prompt_self_assessment`, живёт
    в `linter/prompts.py` и `shared` манифеста; длина признаком не является);
  * блок — **packet-handoff**: задача плана или реализации для репозитория
    (`packet_patterns`: план, реализация, репозиторий, ветка, PR, коммит, тест,
    Claude Code). Правило стоит о шаблонах 2/3 prompt-kit и на DR-промпт с
    упоминанием ADR не распространяется — это его «Допустимое попадание»;
  * блок кодирует решение вольта — ссылка на носитель в любой форме
    (`vault_ref_patterns`): ADR/PDR, «реестр», `§N` **при имени канонического
    документа на той же строке** (контракт, kit, несущий документ, CLAUDE.md),
    `owner decision N=X`, «owner-акт», «решение владельца», дата решения.
    Голый `§N` без имени документа не триггерит: § сторонней бумаги решением
    вольта не является.

Такой блок обязан нести строку `Sources` (`sources_pattern`) и хотя бы
`min_anchors` якорей после неё, а каждый якорь — строку «что ограничивает»:
пункт требует «одна строка на якорь о том, что он ограничивает», и якорь без
неё называет источник, не называя, чем он связывает пакет. Это вторая находка
чекера (`anchor_without_constraint`) — отдельная от отсутствия трейлера.

Трейлер меряется **внутри блока**, а не в прозе вокруг: он уезжает вместе с
промптом в чужую сессию — там и должен быть. Проза хода адресату не видна.

Незаполненный трейлер — не трейлер. Шаблоны 2/3 prompt-kit несут слот
`- [ADR/PDR/spec ids + one line each on what it constrains]`, и выданный с
неснятым слотом промпт кодирует ровно ноль якорей. Поэтому перед счётом якорей
одиночные квадратные скобки маскируются (`[[вики-ссылка]]` — не слот и не
маскируется): якорь, найденный внутри слота-заглушки, — имя формы, а не якорь.

«Допустимые попадания» пункта — промпт, не кодирующий решений вольта, — здесь
есть отсутствие триггера: без ADR/PDR/реестра/§ в теле блока чекер молчит.

Списки форм и пороги — данные манифеста: расширяются без правки модуля.

Чистая функция: ни сети, ни LLM, ни файловых эффектов.
"""

from __future__ import annotations

import re

from ..common import RED, Finding, significant_chars
from ..prompts import prompt_blocks

NAME = "sources_trailer"

DEFAULT_VAULT_REFS = [
    r"\bADR[-\s]?\d+",
    r"\bPDR[-\s]?\d+",
    r"\bреестр\w*",
    # § только при имени канонического документа на той же строке.
    r"§\s*[\dA-ZА-ЯЁ][^\n]{0,80}?\b(?:контракт|kit|кит|несущ|CLAUDE\.md|реестр)",
    r"\b(?:контракт|kit|кит|несущ\w+\s+документ|CLAUDE\.md)[^\n]{0,40}?§\s*[\dA-ZА-ЯЁ]",
    r"\bowner\s+decision\s+\d+\s*=\s*\S+",
    r"\bowner-акт\w*",
    r"\bрешени\w+\s+владельца\b",
    r"\b(?:решени|акт|owner)\w*[^\n]{0,40}\d{4}-\d{2}-\d{2}",
]
# Packet-handoff: задача плана или реализации для репозитория. Правило стоит о
# шаблонах 2/3 prompt-kit, а не о любом промпте со ссылкой на вольт.
DEFAULT_PACKET = [
    r"\bплан\w*\b", r"\bplan\b", r"\bреализ\w*", r"\bimplement",
    r"\bрепозитор\w*", r"\brepo\b", r"\bветк\w*", r"\bbranch\b",
    r"\bPR\b", r"\bкоммит\w*", r"\bтест\w*", r"\bpytest\b",
    r"Claude\s+Code", r"plan[-\s]mode",
]
DEFAULT_SOURCES = r"^\s*(?:[-*+•]\s*)?\**\s*Sources\b"
DEFAULT_ANCHORS = [
    r"\bADR[-\s]?\d+",
    r"\bPDR[-\s]?\d+",
    r"\bEMV-DL[-\s]?\d+",
    r"\bR-[A-ZА-ЯЁ]+-\d+",
    r"§\s*\d+",
    r"\[\[[^\]]+\]\]",
    r"[\w./-]+\.(?:md|ya?ml)\b",
]
# Одиночные скобки — слот шаблона; двойные — вики-ссылка вольта, она якорь.
SLOT = re.compile(r"(?<!\[)\[[^\[\]]*\](?!\])")
# Область трейлера: до первой пустой строки, но не длиннее окна. Прежние 3
# строки обрезали трейлер из четырёх якорей — счёт вёлся по трети списка.
DEFAULT_ANCHOR_WINDOW = 20
DEFAULT_MIN_ANCHORS = 1
# «Что ограничивает»: после последнего якоря строки стоит разделитель и текст
# такой длины. Якорь без него называет источник, не называя связи с пакетом.
DEFAULT_CONSTRAINT_SPLIT = r"[—–:-]"
DEFAULT_MIN_CONSTRAINT_CHARS = 12


def _compile(patterns) -> list[re.Pattern]:
    return [re.compile(p, re.IGNORECASE) for p in patterns]


def _trailer_lines(body: list[str], idx: int, window: int) -> list[tuple[int, str]]:
    """Строки области трейлера как (индекс в теле, текст).

    Хвост строки `Sources` плюс следующие непустые строки, не дальше окна:
    пустая строка кончает список якорей, а окно страхует от списка без конца.
    """
    head = body[idx]
    m = re.match(DEFAULT_SOURCES, head, re.IGNORECASE)
    out = [(idx, head[m.end():] if m else head)]
    for j in range(idx + 1, min(len(body), idx + 1 + window)):
        if not body[j].strip():
            break
        out.append((j, body[j]))
    return out


def _constraint(line: str, anchors: list[re.Pattern], split: re.Pattern,
                min_chars: int) -> bool:
    """Несёт ли строка якоря строку «что ограничивает».

    Мера — текст после последнего якоря, отделённый тире либо двоеточием:
    «ADR-053 §1.4 — ярусы подписи: ограничивает, что правится без владельца».
    Голый якорь («- ADR-053 §1.4») называет источник, не называя связи.
    """
    ends = [m.end() for a in anchors for m in a.finditer(line)]
    if not ends:
        return True                       # строка без якоря — не предмет
    tail = line[max(ends):]
    m = split.search(tail)
    if m is None:
        return False
    return significant_chars(tail[m.end():]) >= min_chars


def check(text: str, config: dict) -> list[Finding]:
    config = config or {}
    vault_refs = _compile(config.get("vault_ref_patterns") or DEFAULT_VAULT_REFS)
    sources_re = re.compile(config.get("sources_pattern", DEFAULT_SOURCES),
                            re.IGNORECASE)
    anchors = _compile(config.get("anchor_patterns") or DEFAULT_ANCHORS)
    packet = _compile(config.get("packet_patterns") or DEFAULT_PACKET)
    window = int(config.get("anchor_window", DEFAULT_ANCHOR_WINDOW))
    min_anchors = int(config.get("min_anchors", DEFAULT_MIN_ANCHORS))
    split_re = re.compile(config.get("constraint_split", DEFAULT_CONSTRAINT_SPLIT))
    min_constraint = int(config.get("min_constraint_chars",
                                    DEFAULT_MIN_CONSTRAINT_CHARS))

    findings: list[Finding] = []

    for block, _seen in prompt_blocks(text, config):
        body = block.lines
        blob = "\n".join(body)
        if not any(p.search(blob) for p in packet):
            continue                      # не packet-handoff: предмета правила нет
        ref = next((v.search(blob) for v in vault_refs if v.search(blob)), None)
        if ref is None:
            continue

        idx = next((i for i, raw in enumerate(body) if sources_re.match(raw)), None)
        if idx is None:
            findings.append(Finding(
                block.fence_line, NAME, RED,
                f"промпт кодирует решение вольта («{ref.group(0).strip()}»), но не "
                f"несёт строки `Sources`: агент вольт не читает, и трасса "
                f"vault → handoff → план обрывается на самом handoff — plan review "
                f"(контракт §8) сверяет «Decisions & assumptions» против пустого списка"))
            continue

        region_lines = [(j, SLOT.sub(" ", raw))
                        for j, raw in _trailer_lines(body, idx, window)]
        region = "\n".join(raw for _j, raw in region_lines)
        found = sorted({m.group(0).strip()
                        for a in anchors for m in a.finditer(region)})
        if len(found) < min_anchors:
            findings.append(Finding(
                block.start + idx, NAME, RED,
                f"строка `Sources` есть, якорей после неё — {len(found)} при "
                f"требуемых {min_anchors}: слот шаблона не снят либо заполнен прозой. "
                f"Незаполненный трейлер кодирует ноль решений вольта и от его "
                f"отсутствия не отличается"))
            continue

        # Якорь без строки «что ограничивает»: источник назван, связь с пакетом —
        # нет. Plan review сверяет «Decisions & assumptions» именно против неё.
        for j, raw in region_lines:
            if _constraint(raw, anchors, split_re, min_constraint):
                continue
            named = ", ".join(sorted({m.group(0).strip() for a in anchors
                                      for m in a.finditer(raw)}))
            findings.append(Finding(
                block.start + j, NAME, RED,
                f"[anchor_without_constraint] якорь «{named}» в трейлере назван без "
                f"строки «что ограничивает»: пункт требует одну строку на якорь о "
                f"том, чем он связывает пакет. Список источников без неё говорит, "
                f"откуда взято, и не говорит, что из этого следует — plan review "
                f"(контракт §8) сверяет «Decisions & assumptions» против связи, "
                f"а не против перечня имён"))
    return findings
