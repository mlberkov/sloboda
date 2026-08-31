"""Базовая механика shell-блока: длина, exit, плейсхолдеры, вложенные ограды.

Опора: контракт §11, «Исполнимость shell-блока», вопросы (1) и (4).
Чистая функция: ни сети, ни LLM, ни файловых эффектов.
"""

from __future__ import annotations

import re

from ..common import RED, Finding, shell_blocks

NAME = "shell_mech"

DEFAULT_PLACEHOLDER = r"<[A-ZА-ЯЁ0-9][A-ZА-ЯЁ0-9_-]{1,}>"
DEFAULT_EXIT = r"(?:^|[;&|]\s*)exit\b"
HEREDOC_OPEN = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")
FENCE_RUN = re.compile(r"`{3,}")


def check(text: str, config: dict) -> list[Finding]:
    config = config or {}
    max_lines = int(config.get("max_block_lines", 25))
    placeholder = re.compile(config.get("placeholder_pattern", DEFAULT_PLACEHOLDER))
    exit_re = re.compile(config.get("exit_pattern", DEFAULT_EXIT))
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

        open_heredocs: list[tuple[int, str]] = []
        for ln, raw in b.numbered():
            if open_heredocs and raw.strip() == open_heredocs[-1][1]:
                open_heredocs.pop()
                continue
            if not open_heredocs:
                m = HEREDOC_OPEN.search(raw)
                if m:
                    open_heredocs.append((ln, m.group(2)))

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

        for ln, tag in open_heredocs:
            findings.append(Finding(
                ln, NAME, RED,
                f"heredoc `{tag}` не закрыт до конца блока: "
                f"ограда закрылась внутри heredoc, блок неисполним"))

    return sorted(findings, key=lambda f: (f.line, f.message))
