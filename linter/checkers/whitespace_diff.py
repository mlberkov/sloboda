"""S-13 / К2: вывод о содержании правки построен на счётчике без фильтра пробелов.

Опора: контракт §11, «Содержательность правки — после фильтра пробелов, не по
счётчикам» (добавлено 2026-08-25, разбор сессии security-каденции; реестр:
R-WHITESPACE-011). Пункт: вывод «файл изменён» не делается по `--stat` без
фильтра — равное число вставок и удалений есть подпись построчной перезаписи
(концы строк, кодировка, нормализация редактором), а не правки. Прежде любого
утверждения о содержании прогоняется `git diff -w --ignore-cr-at-eol`.

Что меряется. В shell-блоке — счётчик правки (`git diff --stat|--numstat|
--shortstat`, `git show --stat`) при отсутствии в том же блоке диффа с фильтром
пробелов (`-w`, `-b`, `--ignore-all-space`, `--ignore-space-change`,
`--ignore-cr-at-eol`). Блок с одним счётчиком отвечает на вопрос «файл
отличается от `HEAD`», а выдаётся под вопрос «что в нём изменилось».

«Допустимые попадания» пункта — утверждения без притязания на содержание —
опознаются формой: пометка рядом с блоком (`hedge_patterns`: «только о факте
различия», «без притязания на содержание», «файл отличается от HEAD»).
Утверждение о содержании само по себе смыслом не опознаётся, поэтому чекер
меряет **блок**: наличие счётчика без фильтра. Половина «утверждение
сопровождается прогоном с фильтром» — за владельцем; S-13 называет это прямо.

Формы счётчика, фильтра и пометки — данные манифеста.

Чистая функция: ни сети, ни LLM, ни файловых эффектов.
"""

from __future__ import annotations

import re

from ..common import RED, Finding, shell_blocks, split_lines

NAME = "whitespace_diff"

DEFAULT_COUNTER = r"\bgit\s+(?:diff|show|log)\b[^\n]*?(?:--stat|--numstat|--shortstat)\b"
DEFAULT_FILTER = (r"\bgit\s+diff\b[^\n]*?(?:(?<![\w-])-w\b|(?<![\w-])-b\b|"
                  r"--ignore-all-space|--ignore-space-change|--ignore-cr-at-eol)")
DEFAULT_HEDGES = [
    r"только\s+о\s+факте\s+различи",
    r"без\s+притязани\w*\s+на\s+содержани",
    r"отличаетс\w*\s+от\s+`?HEAD`?",
]
DEFAULT_HEDGE_WINDOW = 3


def check(text: str, config: dict) -> list[Finding]:
    config = config or {}
    counter_re = re.compile(config.get("counter_pattern", DEFAULT_COUNTER), re.IGNORECASE)
    filter_re = re.compile(config.get("filter_pattern", DEFAULT_FILTER), re.IGNORECASE)
    hedges = [re.compile(p, re.IGNORECASE)
              for p in (config.get("hedge_patterns") or DEFAULT_HEDGES)]
    window = int(config.get("hedge_window", DEFAULT_HEDGE_WINDOW))

    lines = split_lines(text)
    findings: list[Finding] = []
    for b in shell_blocks(text, config):
        if filter_re.search("\n".join(b.lines)):
            continue
        # Пометка ищется вокруг блока: она стоит в прозе хода, не в командах.
        lo = max(0, b.fence_line - 1 - window)
        hi = min(len(lines), b.end + 1 + window)
        around = "\n".join(lines[lo:hi])
        if any(h.search(around) for h in hedges):
            continue
        for ln, raw in b.numbered():
            m = counter_re.search(raw)
            if m is None:
                continue
            findings.append(Finding(
                ln, NAME, RED,
                f"счётчик правки (`{m.group(0).strip()}`) без диффа с фильтром "
                f"пробелов в том же блоке: равное число вставок и удалений — "
                f"подпись построчной перезаписи (концы строк, кодировка, "
                f"нормализация редактором), а не правки; прежде утверждения о "
                f"содержании прогоняется `git diff -w --ignore-cr-at-eol`, иначе "
                f"настоящая правка прячется в шуме перезаписи, а перезапись "
                f"читается как правка"))
            break                     # один счётчик на блок: предмет — блок
    return findings
