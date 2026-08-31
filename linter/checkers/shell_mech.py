"""Базовая механика shell-блока: длина, exit, плейсхолдеры, вложенные ограды,
путь Windows-вида, `cd` без сцепки.

Опора: контракт §11, «Исполнимость shell-блока», вопросы (1) и (4).
Чистая функция: ни сети, ни LLM, ни файловых эффектов.

Две формы, добавленные 2026-08-31 по инциденту того же дня (блок коммита вольта
написан в Windows-синтаксисе и исполнен в bash; `cd D:\\Obsidian\\TheyGrow` съел
бэкслеши и упал, остаток блока отработал в постороннем репозитории и напечатал
«nothing to commit, working tree clean» — провал стал неотличим от успеха):

  windows_path      — путь вида «буква диска + двоеточие + бэкслеш» в блоке,
                      помеченном как исполняемый в bash/WSL;
  cd_without_chain  — `cd` последней командой строки, не сцепленный `&&` со
                      следующей командой блока.

Обе формы — данные: шаблоны и оправдания живут в linter/manifest.yaml и
правятся без правки этого модуля. Обе смотрят только на команды блока: тело
heredoc — вставляемый текст, а не команды, и в нём ни путь, ни `cd` не
исполняются.
"""

from __future__ import annotations

import re

from ..common import RED, Finding, shell_blocks

NAME = "shell_mech"

DEFAULT_PLACEHOLDER = r"<[A-ZА-ЯЁ0-9][A-ZА-ЯЁ0-9_-]{1,}>"
DEFAULT_EXIT = r"(?:^|[;&|]\s*)exit\b"
# Буква диска + двоеточие + бэкслеш и хвост пути до пробела либо кавычки.
DEFAULT_WINDOWS_PATH = r'(?<!\w)[A-Za-z]:\\[^\s"]*'
# `cd`, стоящий последней командой строки: после него до конца строки нет ни
# `&&`, ни `||`, ни `;` — то есть следующая команда блока от его исхода не зависит.
DEFAULT_CD_UNCHAINED = r"(?:^|[;&|]\s*)cd\s+[^;&|]*$"
# `set -e` (в том числе `set -euo pipefail`) выше по блоку: провал `cd` обрывает
# блок сам, остаток в чужом каталоге не исполняется.
DEFAULT_ERREXIT = r"^\s*set\s+-[A-Za-z]*e"

HEREDOC_OPEN = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")
FENCE_RUN = re.compile(r"`{3,}")
COMMENT = re.compile(r"^\s*#")


def _rows(block) -> tuple[list[tuple[int, str, bool]], list[tuple[int, str]]]:
    """Строки блока как (номер, текст, тело_heredoc) + незакрытые heredoc'и.

    Открывающая heredoc строка телом не считается — она команда. Строка-
    терминатор в выдачу не попадает: она и не команда, и не тело.
    """
    rows: list[tuple[int, str, bool]] = []
    open_heredocs: list[tuple[int, str]] = []
    for ln, raw in block.numbered():
        if open_heredocs and raw.strip() == open_heredocs[-1][1]:
            open_heredocs.pop()
            continue
        inside = bool(open_heredocs)
        if not inside:
            m = HEREDOC_OPEN.search(raw)
            if m:
                open_heredocs.append((ln, m.group(2)))
        rows.append((ln, raw, inside))
    return rows, open_heredocs


def check(text: str, config: dict) -> list[Finding]:
    config = config or {}
    max_lines = int(config.get("max_block_lines", 25))
    placeholder = re.compile(config.get("placeholder_pattern", DEFAULT_PLACEHOLDER))
    exit_re = re.compile(config.get("exit_pattern", DEFAULT_EXIT))
    win_re = re.compile(config.get("windows_path_pattern", DEFAULT_WINDOWS_PATH))
    cd_re = re.compile(config.get("cd_unchained_pattern", DEFAULT_CD_UNCHAINED))
    errexit_re = re.compile(config.get("errexit_pattern", DEFAULT_ERREXIT))
    findings: list[Finding] = []

    for b in shell_blocks(text, config):
        if len(b.lines) > max_lines:
            findings.append(Finding(
                b.fence_line, NAME, RED,
                f"shell-блок длиной {len(b.lines)} строк (> {max_lines}): "
                f"не переживает вставку и разметку ответа"))
        if not b.closed:
            findings.append(Finding(
                b.fence_line, NAME, RED,
                "ограда блока не закрыта — блок не переживает вставку"))

        rows, open_heredocs = _rows(b)

        for ln, raw, _inside in rows:
            if exit_re.search(raw):
                findings.append(Finding(
                    ln, NAME, RED,
                    "`exit` в блоке: закрывает оболочку владельца, "
                    "остаток блока не исполняется и об этом не сообщается"))
            for pm in placeholder.finditer(raw):
                findings.append(Finding(
                    ln, NAME, RED,
                    f"плейсхолдер {pm.group(0)}: блок не исполним как есть — "
                    f"значение генерируется командой, а не пишется текстом"))
            if FENCE_RUN.search(raw):
                findings.append(Finding(
                    ln, NAME, RED,
                    "вложенная ограда ``` внутри shell-блока: "
                    "разметка ответа рвёт блок на месте вставки"))

        # Форма windows_path: путь Windows-вида среди команд bash/WSL-блока.
        for ln, raw, inside in rows:
            if inside:
                continue
            for wm in win_re.finditer(raw):
                findings.append(Finding(
                    ln, NAME, RED,
                    f"[windows_path] путь Windows-вида `{wm.group(0)}` в блоке, "
                    f"помеченном как исполняемый в bash/WSL: оболочка съедает "
                    f"бэкслеши, путь до каталога не доходит и `cd` падает — "
                    f"остаток блока отрабатывает в текущем каталоге и печатает "
                    f"правдоподобный успех; в WSL диск адресуется как `/mnt/d/…`"))

        # Форма cd_without_chain: `cd` последней командой строки при том, что
        # ниже в блоке есть ещё команды.
        for pos, (ln, raw, inside) in enumerate(rows):
            if inside or COMMENT.match(raw):
                continue
            m = cd_re.search(raw)
            if not m:
                continue
            if any(errexit_re.search(prev) for _pl, prev, pi in rows[:pos] if not pi):
                continue
            rest = [r for _rl, r, ri in rows[pos + 1:]
                    if not ri and r.strip() and not COMMENT.match(r)]
            if not rest:
                continue
            findings.append(Finding(
                ln, NAME, RED,
                f"[cd_without_chain] `{m.group(0).strip().lstrip(';&| ')}` — "
                f"`cd` отдельной командой, не сцепленный `&&` со следующей "
                f"({len(rest)} команд ниже по блоку): при провале `cd` остаток "
                f"блока исполняется в чужом каталоге и печатает правдоподобный "
                f"успех — провал неотличим от успеха"))

        for ln, tag in open_heredocs:
            findings.append(Finding(
                ln, NAME, RED,
                f"heredoc `{tag}` не закрыт до конца блока: "
                f"ограда закрылась внутри heredoc, блок неисполним"))

    return sorted(findings, key=lambda f: (f.line, f.message))
