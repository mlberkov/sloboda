"""S-12 / К3: `gh` после ухода из рабочего дерева без явного `--repo`.

Опора: контракт §11, «`gh` вне рабочего дерева — с явным `--repo`» (добавлено
2026-08-23, разбор milestone PPR; реестр: R-GHREPO-010). Пункт: любая
`gh`-команда в блоке, который перед ней покидает рабочее дерево (`cd /tmp/...`
и аналоги), несёт явный `--repo <owner>/<repo>`: без git-контекста `gh` не
определяет репозиторий и **падает на этом, а не на предмете**. Повод: выгрузка
ассетов релиза `v0.1.221` в `/tmp` упала с `failed to determine base repo` —
раунд потерян на дефекте блока, не предмета.

Что меряется. В shell-блоке: строка, уводящая в каталог вне дерева
(`leave_patterns` — `/tmp`, `/var/tmp`, `$TMPDIR`, `$(mktemp -d)`, `cd ..`), и
**ниже неё** вызов `gh` без `--repo`/`-R`. Оправдание: `--repo` на самой строке
либо `GH_REPO=` выше по блоку — переменная окружения даёт `gh` тот же контекст.

Граница меры названа честно: «вне дерева» опознаётся по перечню каталогов-
черновиков из манифеста, а не разбором файловой системы. `cd ~/altrego` — уход
в другой репозиторий, а не из дерева, и здесь не краснеет: какой каталог
является рабочим деревом, из текста артефакта не выводится. Эта половина
остаётся за владельцем и названа в S-12.

Чистая функция: ни сети, ни LLM, ни файловых эффектов.
"""

from __future__ import annotations

import re

from ..common import RED, Finding, shell_blocks

NAME = "gh_repo_scope"

# Уход в каталог-черновик: дословные формы повода пункта.
DEFAULT_LEAVE = [
    r"(?:^|[;&|]\s*)cd\s+[\"']?(/tmp|/var/tmp)\b",
    r"(?:^|[;&|]\s*)cd\s+[\"']?\$\{?TMPDIR",
    r"(?:^|[;&|]\s*)cd\s+[\"']?\$\(mktemp",
    r"(?:^|[;&|]\s*)cd\s+[\"']?\.\.(?:/|\s|$)",
]
DEFAULT_GH = r"(?<![\w-])gh\s+[a-z]"
DEFAULT_ABSOLVED_LINE = r"--repo\b|(?<![\w-])-R\s+\S"
DEFAULT_ABSOLVED_BLOCK = r"\bGH_REPO="


def check(text: str, config: dict) -> list[Finding]:
    config = config or {}
    leave = [re.compile(p, re.IGNORECASE)
             for p in (config.get("leave_patterns") or DEFAULT_LEAVE)]
    gh_re = re.compile(config.get("gh_pattern", DEFAULT_GH))
    line_ok = re.compile(config.get("absolved_same_line", DEFAULT_ABSOLVED_LINE))
    block_ok = re.compile(config.get("absolved_in_block", DEFAULT_ABSOLVED_BLOCK))

    findings: list[Finding] = []
    for b in shell_blocks(text, config):
        if block_ok.search("\n".join(b.lines)):
            continue
        left_at: tuple[int, str] | None = None
        for ln, raw in b.numbered():
            if left_at is None:
                hit = next((p.search(raw) for p in leave if p.search(raw)), None)
                if hit is not None:
                    left_at = (ln, hit.group(0).strip())
                    continue
            if left_at is None or not gh_re.search(raw) or line_ok.search(raw):
                continue
            findings.append(Finding(
                ln, NAME, RED,
                f"`gh` вызван после ухода из рабочего дерева (`{left_at[1]}` "
                f"на строке {left_at[0]}) без `--repo`: вне git-контекста `gh` не "
                f"определяет репозиторий и падает на этом, а не на предмете — "
                f"провал блока неотличим от отсутствия предмета"))
    return findings
