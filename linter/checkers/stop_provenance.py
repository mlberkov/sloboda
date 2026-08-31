"""S-02 / К1: числовой литерал в блоке стоп-условия без соседнего провенанса.

Опора: контракт §11, «Провенанс утверждений оркестратора»;
реестр правок канона §B — рецидив 2026-08-27, `AHEAD_OF_MAIN: 7`, счёт 2.
"""

from __future__ import annotations

import re

from ..common import RED, Finding, mask_spans, split_lines

NAME = "stop_provenance"

DEFAULT_MARKERS = [
    r"стоп-услов",
    r"стоп услов",
    r"не должно двигаться",
    r"должно совпасть",
    r"stop[- ]condition",
]
DEFAULT_IGNORE = [
    r"§\s*\d+(\.\d+)*",
    r"\b\d{4}-\d{2}-\d{2}\b",
    r"\b(ADR|PDR|EMV-DL|S|К|L)-?\d+\b",
    r"\bp?\.?\s*\d+\s*(строк|строки|строка)\b",
    r"\bv\d+(\.\d+)*\b",
    r"\bПРИМЕР\b",
]
DEFAULT_COMMAND = (
    r"`[^`]*\b(git|gh|wc|grep|rg|sed|awk|jq|curl|ls|cat|find|gcloud|adb|docker|"
    r"kubectl|python3?|pip|npm|test)\b[^`]*`"
)
DEFAULT_AWAITING = r"(ожидан|ожидается|не проверено|не измерено|оценк|прогноз|предполож)"
DEFAULT_OUTPUT = r"(выдач|вывод|измерено|провенанс|из выдачи|вернул|отдал|показал)"
NUMBER = re.compile(r"(?<![\w.])\d+(?![\w.])")
LIST_ITEM = re.compile(r"^\s*([-*+]|\d+[.)])\s+")


def _blocks(lines: list[str], markers: list[re.Pattern], max_lines: int):
    """Диапазоны (start_idx, end_idx) блоков стоп-условия, 0-индексные, включительно."""
    out = []
    i = 0
    while i < len(lines):
        if any(m.search(lines[i]) for m in markers):
            j = i
            blank_run = 0
            while j + 1 < len(lines) and (j - i) < max_lines:
                nxt = lines[j + 1]
                if not nxt.strip():
                    blank_run += 1
                    if blank_run > 1:
                        break
                    # пустая строка терпима, если дальше продолжается список
                    k = j + 2
                    if k >= len(lines) or not LIST_ITEM.match(lines[k]):
                        break
                    j += 1
                    continue
                if re.match(r"^#{1,6}\s", nxt):
                    break
                blank_run = 0
                j += 1
            out.append((i, j))
            i = j + 1
        else:
            i += 1
    return out


def check(text: str, config: dict) -> list[Finding]:
    config = config or {}
    markers = [re.compile(p, re.IGNORECASE)
               for p in (config.get("block_markers") or DEFAULT_MARKERS)]
    ignore = config.get("ignore_patterns") or DEFAULT_IGNORE
    command = re.compile(config.get("command_pattern", DEFAULT_COMMAND), re.IGNORECASE)
    awaiting = re.compile(config.get("awaiting_pattern", DEFAULT_AWAITING), re.IGNORECASE)
    output = re.compile(config.get("output_pattern", DEFAULT_OUTPUT), re.IGNORECASE)
    window = int(config.get("provenance_window", 1))
    max_lines = int(config.get("block_max_lines", 20))

    lines = split_lines(text)
    findings: list[Finding] = []

    for start, end in _blocks(lines, markers, max_lines):
        for idx in range(start, end + 1):
            masked = mask_spans(lines[idx], ignore)
            nums = list(NUMBER.finditer(masked))
            if not nums:
                continue
            lo, hi = max(start, idx - window), min(end, idx + window)
            near = "\n".join(lines[lo:hi + 1])
            if command.search(near) or awaiting.search(near) or output.search(near):
                continue
            findings.append(Finding(
                idx + 1, NAME, RED,
                f"число {nums[0].group(0)} в блоке стоп-условия без соседнего провенанса: "
                f"рядом нет ни команды, его вернувшей, ни цитаты выдачи, "
                f"ни пометки «ожидание»/«не проверено»"))
    return findings
