"""S-11 / К3: выгрузка артефактов без явного каталога вне рабочего дерева.

Опора: контракт §11, «Выгрузка артефактов — вне рабочего дерева» (добавлено
2026-08-22, разбор закрытия L3; дополняет «Исполнимость shell-блока»; реестр:
R-DOWNLOAD-009). Пункт: `gh run download` и любой аналог в handoff-блоке
**всегда** несёт явный каталог назначения вне репозитория (`-D /tmp/...`).
*Допустимые попадания:* нет — единственный пункт корпуса с пустой строкой здесь,
поэтому у чекера нет оправдывающей формы, кроме маркера изъятия.

Что меряется — две формы одного отказа:

  * вызов выгрузки без `-D`/`--dir` вовсе: артефакты лягут в текущий каталог,
    то есть в рабочее дерево;
  * `-D` с относительным путём: каталог назначения всё равно внутри дерева.
    «Вне дерева» опознаётся по началу пути (`outside_prefixes`: `/`, `~`,
    `$TMPDIR`, `$(mktemp …)`), а не по разбору файловой системы — линтер читает
    текст, а не диск.

Повод пункта: на закрытии L3 шесть каталогов артефактов (включая релизный APK)
легли в корень рабочего дерева, не были в `.gitignore` и уцелели только потому,
что коммиты собирались перечислением путей, а не `git add -A`.

Формы вызова и префиксы «вне дерева» — данные манифеста.

Чистая функция: ни сети, ни LLM, ни файловых эффектов.
"""

from __future__ import annotations

import re

from ..common import RED, Finding, shell_blocks

NAME = "download_dir"

DEFAULT_DOWNLOAD = r"\bgh\s+(?:run|release)\s+download\b"
DEFAULT_DIR = r"(?:-D|--dir)[=\s]+(\S+)"
# Начала путей, ведущих заведомо вне рабочего дерева.
DEFAULT_OUTSIDE = [r"^/", r"^~", r"^\$TMPDIR", r"^\$\{TMPDIR", r"^\$\(mktemp"]


def check(text: str, config: dict) -> list[Finding]:
    config = config or {}
    dl_re = re.compile(config.get("download_pattern", DEFAULT_DOWNLOAD), re.IGNORECASE)
    dir_re = re.compile(config.get("dir_pattern", DEFAULT_DIR))
    outside = [re.compile(p) for p in (config.get("outside_prefixes") or DEFAULT_OUTSIDE)]

    findings: list[Finding] = []
    for b in shell_blocks(text, config):
        for ln, raw in b.numbered():
            if not dl_re.search(raw):
                continue
            m = dir_re.search(raw)
            if m is None:
                findings.append(Finding(
                    ln, NAME, RED,
                    "выгрузка артефактов без `-D`/`--dir`: каталог назначения — "
                    "текущий, то есть рабочее дерево; артефакты лягут в репозиторий, "
                    "мимо `.gitignore`, и уедут в коммит на первом `git add -A` "
                    "(§11 требует явный каталог вне дерева, допустимых попаданий нет)"))
                continue
            path = m.group(1).strip().strip('"\'')
            if any(p.match(path) for p in outside):
                continue
            findings.append(Finding(
                ln, NAME, RED,
                f"выгрузка в `{path}` — относительный путь, то есть внутрь рабочего "
                f"дерева: `-D` назван, но каталог назначения остался в репозитории "
                f"(§11 требует путь вне дерева, например `-D /tmp/…`)"))
    return findings
