"""Общие структуры и разбор артефакта. Чистые функции, без сети и файловых эффектов."""

from __future__ import annotations

import re
from dataclasses import dataclass, asdict

RED = "red"
WARNING = "warning"
# Изъятие без причины — не вердикт чекера о предмете, а дефект самой разметки
# артефакта; severity отдельная, но валит прогон наравне с красным.
ERROR = "error"
FAILING = frozenset({RED, ERROR})

FENCE = re.compile(r"^(\s*)(`{3,}|~{3,})\s*([^\s`]*)")


@dataclass(frozen=True)
class Finding:
    line: int
    checker: str
    severity: str
    message: str

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Block:
    """Огороженный блок артефакта."""
    lang: str
    fence_line: int          # 1-индексная строка открывающей ограды
    start: int               # 1-индексная первая строка содержимого
    end: int                 # 1-индексная последняя строка содержимого (включительно)
    lines: list[str]         # содержимое блока
    closed: bool
    is_shell: bool

    def numbered(self):
        """Пары (абсолютный 1-индексный номер строки, текст)."""
        return zip(range(self.start, self.start + len(self.lines)), self.lines)


def split_lines(text: str) -> list[str]:
    return text.replace("\r\n", "\n").replace("\r", "\n").split("\n")


def parse_blocks(text: str, config: dict | None = None) -> list[Block]:
    """Огороженные блоки. Закрывающая ограда — не короче открывающей, без языка."""
    config = config or {}
    shell_langs = set(config.get("shell_langs") or
                      ["bash", "sh", "shell", "zsh", "console", "shell-session"])
    handoff_markers = config.get("handoff_markers") or ["Handoff for shell", "Handoff for git"]
    lookback = int(config.get("handoff_lookback", 3))

    lines = split_lines(text)
    blocks: list[Block] = []
    i = 0
    while i < len(lines):
        m = FENCE.match(lines[i])
        if not m:
            i += 1
            continue
        marker, lang = m.group(2), m.group(3).lower()
        j = i + 1
        closed = False
        while j < len(lines):
            m2 = FENCE.match(lines[j])
            if m2 and m2.group(2)[0] == marker[0] and len(m2.group(2)) >= len(marker) \
               and not m2.group(3):
                closed = True
                break
            j += 1
        body = lines[i + 1:j]
        near = " ".join(lines[max(0, i - lookback):i])
        is_shell = lang in shell_langs or any(h in near for h in handoff_markers)
        blocks.append(Block(lang=lang, fence_line=i + 1, start=i + 2,
                            end=(i + 1 + len(body)), lines=body,
                            closed=closed, is_shell=is_shell))
        i = j + 1 if closed else j
    return blocks


def shell_blocks(text: str, config: dict | None = None) -> list[Block]:
    return [b for b in parse_blocks(text, config) if b.is_shell]


def in_block_lines(text: str, config: dict | None = None) -> set[int]:
    """Номера строк, лежащих внутри любого огороженного блока (вместе с оградами)."""
    out: set[int] = set()
    for b in parse_blocks(text, config):
        out.update(range(b.fence_line, b.end + 2))
    return out


def mask_spans(line: str, patterns: list[str]) -> str:
    """Заменяет совпадения на пробелы — чтобы не считать их числами/формами."""
    out = line
    for p in patterns:
        out = re.sub(p, lambda m: " " * len(m.group(0)), out)
    return out


def significant_chars(s: str) -> int:
    return sum(1 for ch in s if ch.isalnum())
