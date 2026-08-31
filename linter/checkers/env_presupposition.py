"""S-03 / К2–К3: предпосылочная форма без предшествующей измеряющей строки.

Опора: контракт §11, «Исполнимость shell-блока», вопрос (2) «Предпосылки»
и (2а) «Гейт — предпосылка блока».
Реестр правок канона §B: рецидив 2026-08-28 — имя сервиса Cloud Run по памяти.

Список форм — данные, а не код: он живёт в linter/manifest.yaml (`forms`) и
расширяется без правки этого модуля. Виды форм:
  kind: line_pattern     — строка совпала с `pattern`; оправдана, если на ней же
                           есть `absolved_by_line` либо выше по блоку есть
                           `absolved_by_above` (измеряющая строка).
  kind: heredoc_length   — heredoc длиннее `max_lines` строк.
Область (`scope`): shell_block (по умолчанию) либо document — форма ловится в
любой строке артефакта, включая прозу и inline-код.
"""

from __future__ import annotations

import re

from ..common import RED, Finding, shell_blocks, split_lines

NAME = "env_presupposition"

HEREDOC_OPEN = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")

DEFAULT_FORMS = [
    {
        "name": "pip_install_outside_venv",
        "kind": "line_pattern",
        "scope": "document",
        "pattern": r"\bpip3?\s+install\b",
        "absolved_by_line": r"(\.venv/bin/pip|/venv/bin/pip|\bvenv/bin/pip|"
                            r"python3?\s+-m\s+venv|--python\s+\S*venv)",
        "absolved_by_above": r"(python3?\s+-m\s+venv|source\s+\S*venv/bin/activate|"
                             r"\.\s+\S*venv/bin/activate|VIRTUAL_ENV=)",
        "above_window": 3,
        "message": "`pip install` без venv-пути и без предшествующей строки, создающей "
                   "или активирующей venv: в среде PEP 668 системный pip отказывает "
                   "(externally-managed-environment) — блок падает на предпосылке, "
                   "а не на предмете",
    },
    {
        "name": "gcloud_run_named_service",
        "kind": "line_pattern",
        "scope": "shell_block",
        "pattern": r"\bgcloud\s+run\s+(deploy|revisions|services\s+"
                   r"(update|describe|delete|update-traffic))\b",
        "absolved_by_above": r"\bgcloud\s+run\s+services\s+list\b",
        "message": "`gcloud run …` с именем сервиса без предшествующего "
                   "`gcloud run services list`: имя взято по памяти, "
                   "провал блока неотличим от отсутствия предмета",
    },
    {
        "name": "adb_serial_without_devices",
        "kind": "line_pattern",
        "scope": "shell_block",
        "pattern": r"\badb\s+-s\s+\S+",
        "absolved_by_above": r"\badb\s+devices\b",
        "message": "`adb -s <серийник>` без предшествующего `adb devices`: "
                   "серийник назван по памяти, подключение не измерено",
    },
    {
        "name": "long_heredoc",
        "kind": "heredoc_length",
        "scope": "shell_block",
        "max_lines": 25,
        "message": "heredoc длиннее {max_lines} строк: предпосылка о том, что канал "
                   "доставки переживёт вставку такой длины, не измерена",
    },
]


def _line_pattern(form: dict, spans: list[tuple[str, list[tuple[int, str]]]]) -> list[Finding]:
    pat = re.compile(form["pattern"])
    by_line = form.get("absolved_by_line")
    by_line_re = re.compile(by_line) if by_line else None
    by_above = form.get("absolved_by_above")
    by_above_re = re.compile(by_above) if by_above else None
    # Сколько строк выше считаются «измеряющими». None — весь охват (блок/документ).
    above_window = form.get("above_window")

    out: list[Finding] = []
    for _scope_id, numbered in spans:
        for pos, (ln, raw) in enumerate(numbered):
            if not pat.search(raw):
                continue
            if by_line_re and by_line_re.search(raw):
                continue
            lo = 0 if above_window is None else max(0, pos - int(above_window))
            if by_above_re and any(by_above_re.search(prev)
                                   for _, prev in numbered[lo:pos]):
                continue
            out.append(Finding(ln, NAME, RED,
                               f"[{form['name']}] " + form["message"]))
    return out


def _heredoc_length(form: dict, spans) -> list[Finding]:
    limit = int(form.get("max_lines", 25))
    out: list[Finding] = []
    for _scope_id, numbered in spans:
        open_at = None
        tag = None
        count = 0
        for ln, raw in numbered:
            if open_at is None:
                m = HEREDOC_OPEN.search(raw)
                if m:
                    open_at, tag, count = ln, m.group(2), 0
                continue
            if raw.strip() == tag:
                if count > limit:
                    out.append(Finding(
                        open_at, NAME, RED,
                        f"[{form['name']}] " +
                        form["message"].format(max_lines=limit) +
                        f" (тело: {count} строк)"))
                open_at, tag, count = None, None, 0
                continue
            count += 1
        if open_at is not None and count > limit:
            out.append(Finding(
                open_at, NAME, RED,
                f"[{form['name']}] " + form["message"].format(max_lines=limit) +
                f" (тело: {count} строк, терминатор не найден)"))
    return out


def check(text: str, config: dict) -> list[Finding]:
    config = config or {}
    forms = config.get("forms") or DEFAULT_FORMS

    doc_lines = split_lines(text)
    doc_span = [("document", list(enumerate(doc_lines, start=1)))]
    blk_span = [(f"block@{b.fence_line}", list(b.numbered()))
                for b in shell_blocks(text, config)]

    findings: list[Finding] = []
    for form in forms:
        spans = doc_span if form.get("scope", "shell_block") == "document" else blk_span
        kind = form.get("kind", "line_pattern")
        if kind == "line_pattern":
            findings.extend(_line_pattern(form, spans))
        elif kind == "heredoc_length":
            findings.extend(_heredoc_length(form, spans))
    return sorted(findings, key=lambda f: (f.line, f.message))
