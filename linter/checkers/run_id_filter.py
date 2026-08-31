"""S-10 / К2: «последний прогон» выбран позицией, а не фильтром события и sha.

Опора: контракт §11, «Идентификатор прогона — фильтром, не позицией»
(добавлено 2026-08-22, разбор закрытия L3; реестр: R-RUNID-008). Пункт: когда
на ветке возможны прогоны разных событий (push и `workflow_dispatch` на одном
sha), прогон выбирается фильтром по событию **и** `headSha`, а не `--limit 1`.

Что меряется. В shell-блоке команда чтения прогона (`gh run list|view|download`),
отбирающая прогон **позицией** — `--limit 1`, `-L 1`, `| head -1`, `.[0]` в
jq-выражении, — при отсутствии в том же блоке фильтра по событию и по sha.
Оба списка форм — данные манифеста.

Почему две ноги оправдания, а не одна: пункт требует именно пары. Фильтр по
событию без sha берёт последний диспатч, но не тот, что на нужном коммите;
фильтр по sha без события возвращает push-прогон того же коммита. Пункт назван
по обеим ногам, чекер меряет обе.

Что остаётся владельцу. Вторая половина «Проверки» — «вердикт по прогону
называет его событие» — из текста блока не выводится: вердикт живёт в прозе
хода и опознаётся смыслом, а не формой. Здесь не меряется; S-10 называет это
прямо. «Допустимые попадания» пункта — контексты с единственно возможным типом
прогона — из артефакта тоже не видны и снимаются маркером изъятия с причиной.

Чистая функция: ни сети, ни LLM, ни файловых эффектов.
"""

from __future__ import annotations

import re

from ..common import RED, Finding, shell_blocks

NAME = "run_id_filter"

DEFAULT_READ = r"\bgh\s+run\s+(?:list|view|download)\b"
# Отбор позицией: «первый в списке», как бы он ни был записан.
DEFAULT_POSITIONAL = [
    r"(?:--limit|-L)\s+1\b",
    r"\|\s*head\s+(?:-n\s*)?-?1\b",
    r"\.\[0\]",
    r"\bfirst\b\s*\(",
]
DEFAULT_EVENT = r"--event\b|--json[^|]*\bevent\b|\bevent\s*=="
DEFAULT_SHA = r"--commit\b|(?<!\w)-c\s+\S|\bheadSha\b|\bhead_sha\b"


def _compile(patterns) -> list[re.Pattern]:
    return [re.compile(p, re.IGNORECASE) for p in patterns]


def check(text: str, config: dict) -> list[Finding]:
    config = config or {}
    read_re = re.compile(config.get("read_pattern", DEFAULT_READ), re.IGNORECASE)
    positional = _compile(config.get("positional_patterns") or DEFAULT_POSITIONAL)
    event_re = re.compile(config.get("event_pattern", DEFAULT_EVENT), re.IGNORECASE)
    sha_re = re.compile(config.get("sha_pattern", DEFAULT_SHA), re.IGNORECASE)

    findings: list[Finding] = []
    for b in shell_blocks(text, config):
        body = "\n".join(b.lines)
        has_event = bool(event_re.search(body))
        has_sha = bool(sha_re.search(body))
        if has_event and has_sha:
            continue
        for ln, raw in b.numbered():
            if not read_re.search(raw):
                continue
            hit = next((p.search(raw) for p in positional if p.search(raw)), None)
            if hit is None:
                continue
            missing = ([] if has_event else ["событию (`--event`)"]) + \
                      ([] if has_sha else ["sha (`--commit`/`headSha`)"])
            findings.append(Finding(
                ln, NAME, RED,
                f"прогон отобран позицией (`{hit.group(0).strip()}`), а фильтра по "
                f"{' и по '.join(missing)} в блоке нет: когда на ветке возможны "
                f"push и `workflow_dispatch` на одном sha, «последний» вернёт "
                f"прогон чужого события — вердикт будет построен не по тому предмету"))
    return findings
