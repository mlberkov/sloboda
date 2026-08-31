"""S-22 / К6: копируемый промпт кодирует решения вольта без Sources-трейлера.

Опора: prompt-kit, раздел «Правила использования», пункт «Sources-трейлер»
(добавлено 2026-07-28, У1; реестр: R-SOURCES-020). Пункт: packet-handoff'ы несут
заполненный оркестратором блок `Sources` — список vault-якорей (ADR/PDR/спека
плюс одна строка «что ограничивает»), которые handoff кодирует; **агент вольт не
читает**, и трейлер — единственная трасса vault → handoff → план. Второй
читатель трейлера назван там же: plan review контракта §8 сверяет «Decisions &
assumptions» плана против этого списка. Без списка сверять не с чем: план
проходит ревью против пустого множества и выглядит согласованным.

Что меряется. Копируемый промпт (признак общий с `prompt_self_assessment`,
живёт в `shared` манифеста: блок длиннее `prompt_min_lines` строк с адресацией
исполнителю), в теле которого названо решение вольта — `vault_ref_patterns`:
ADR/PDR, реестр, § канона. Такой блок обязан нести строку `Sources`
(`sources_pattern`) и хотя бы `min_anchors` якорей после неё.

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

from ..common import RED, Finding
from .prompt_self_assessment import prompt_blocks

NAME = "sources_trailer"

DEFAULT_VAULT_REFS = [
    r"\bADR[-\s]?\d+",
    r"\bPDR[-\s]?\d+",
    r"\bреестр\w*",
    r"§\s*\d+",
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
DEFAULT_ANCHOR_WINDOW = 3
DEFAULT_MIN_ANCHORS = 1


def _compile(patterns) -> list[re.Pattern]:
    return [re.compile(p, re.IGNORECASE) for p in patterns]


def _trailer_region(body: list[str], idx: int, window: int) -> str:
    """Хвост строки `Sources` плюс следующие непустые строки, не дальше window."""
    head = body[idx]
    m = re.match(DEFAULT_SOURCES, head, re.IGNORECASE)
    region = [head[m.end():] if m else head]
    for j in range(idx + 1, min(len(body), idx + 1 + window)):
        if not body[j].strip():
            break
        region.append(body[j])
    return "\n".join(region)


def check(text: str, config: dict) -> list[Finding]:
    config = config or {}
    vault_refs = _compile(config.get("vault_ref_patterns") or DEFAULT_VAULT_REFS)
    sources_re = re.compile(config.get("sources_pattern", DEFAULT_SOURCES),
                            re.IGNORECASE)
    anchors = _compile(config.get("anchor_patterns") or DEFAULT_ANCHORS)
    window = int(config.get("anchor_window", DEFAULT_ANCHOR_WINDOW))
    min_anchors = int(config.get("min_anchors", DEFAULT_MIN_ANCHORS))

    findings: list[Finding] = []

    for block, said in prompt_blocks(text, config):
        body = block.lines
        blob = "\n".join(body)
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

        region = SLOT.sub(" ", _trailer_region(body, idx, window))
        found = sorted({m.group(0).strip()
                        for a in anchors for m in a.finditer(region)})
        if len(found) < min_anchors:
            findings.append(Finding(
                block.start + idx, NAME, RED,
                f"строка `Sources` есть, якорей после неё — {len(found)} при "
                f"требуемых {min_anchors}: слот шаблона не снят либо заполнен прозой. "
                f"Незаполненный трейлер кодирует ноль решений вольта и от его "
                f"отсутствия не отличается"))
    return findings
