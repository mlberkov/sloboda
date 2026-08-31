"""Разбор канона вольта и вычисление content_hash секции пункта.

Чистая библиотека: читает файлы вольт-клона, ничего не пишет и не ходит в сеть.

Модель уровней (см. config.yaml, canon.section_parser: bold_lead_v0):

  уровень 1  markdown-заголовок:  ^#{1,6}\\s
  уровень 2  «пункт» канона:      строка, начинающаяся с ``**``, стоящая первой
             в абзац-блоке (предыдущая строка пуста либо является markdown-
             заголовком). Её жирный зачин — дословный заголовок пункта.
  уровень 3  продолжение пункта:  строка с ``**`` внутри того же абзац-блока
             («Расширение …», «Дополнение …»). Входит в секцию пункта.

Секция пункта = строки от его заголовка до строки перед следующим заголовком
уровня <= 2 (или до конца файла).

Нормализация перед хэшированием:
  * каждая строка обрезается справа (rstrip);
  * хвостовые пустые строки отбрасываются;
  * строки склеиваются через LF;
  * utf-8 → sha256 → hex.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass

MD_HEADING = re.compile(r"^#{1,6}\s")
BOLD_LEAD = re.compile(r"^\*\*(.+?)\*\*", re.DOTALL)

UNRESOLVED = "UNRESOLVED"


@dataclass(frozen=True)
class Section:
    title: str          # дословный жирный зачин заголовка пункта
    start_line: int     # 1-индексная строка заголовка
    end_line: int       # 1-индексная последняя строка секции (включительно)
    text: str           # нормализованный текст секции
    content_hash: str   # sha256 нормализованного текста


def read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def normalize(lines: list[str]) -> str:
    out = [ln.rstrip() for ln in lines]
    while out and not out[-1]:
        out.pop()
    return "\n".join(out)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _level(lines: list[str], idx: int) -> int:
    """Уровень строки idx: 1 — md-заголовок, 2 — пункт, 3 — продолжение, 0 — проза."""
    line = lines[idx]
    if MD_HEADING.match(line):
        return 1
    if not line.startswith("**"):
        return 0
    prev = lines[idx - 1] if idx > 0 else ""
    if idx == 0 or not prev.strip() or MD_HEADING.match(prev):
        return 2
    return 3


def parse_sections(text: str) -> list[Section]:
    """Все пункты (уровень 2) файла с их секциями."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    levels = [_level(lines, i) for i in range(len(lines))]
    starts = [i for i, lv in enumerate(levels) if lv == 2]

    sections: list[Section] = []
    for i in starts:
        end = len(lines)
        for j in range(i + 1, len(lines)):
            if levels[j] in (1, 2):
                end = j
                break
        body = lines[i:end]
        norm = normalize(body)
        m = BOLD_LEAD.match(lines[i])
        title = m.group(1).strip() if m else lines[i].strip()
        # last non-blank line of the section, 1-indexed
        last = i
        for j in range(end - 1, i - 1, -1):
            if lines[j].strip():
                last = j
                break
        sections.append(
            Section(
                title=title,
                start_line=i + 1,
                end_line=last + 1,
                text=norm,
                content_hash=sha256_text(norm),
            )
        )
    return sections


def find_section(text: str, title: str) -> Section | None:
    """Ищет пункт по дословному заголовку; при промахе — по префиксу до ' (добавлено'."""
    sections = parse_sections(text)
    for s in sections:
        if s.title == title:
            return s
    key = title.split(" (добавлено")[0].strip().rstrip(".")
    for s in sections:
        if s.title.split(" (добавлено")[0].strip().rstrip(".") == key:
            return s
    return None


def resolve_path(clone_path: str, rel: str) -> str:
    return os.path.join(os.path.expanduser(clone_path), rel)


def hash_rule(clone_path: str, rel_file: str, heading: str) -> tuple[str, Section | None]:
    """content_hash пункта из вольт-клона. UNRESOLVED, если файла или пункта нет."""
    path = resolve_path(clone_path, rel_file)
    if not os.path.isfile(path):
        return UNRESOLVED, None
    section = find_section(read_text(path), heading)
    if section is None:
        return UNRESOLVED, None
    return section.content_hash, section
