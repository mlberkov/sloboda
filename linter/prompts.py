"""Признак копируемого промпта: инвариант, а не форма инцидента.

Дом признака общий у двух чекеров — `prompt_self_assessment` (промпт есть, а
самооценки нет) и `sources_trailer` (промпт кодирует решение вольта, а трейлера
нет). Две копии признака разошлись бы молча, и один чекер перестал бы видеть
ровно те блоки, по которым краснеет другой. Списки форм живут в `shared`
манифеста, разбор — здесь.

Что было и почему сменилось (реестр правок канона §D, 2026-08-31, «триггер по
форме против триггера по инварианту», третий случай). Первая формулировка
признака описывала инцидент, на котором чекер заводился: огороженный блок
**длиннее 15 строк** с адресацией «Задача:» / «Твоя задача» / «Your task» /
«В ~/». Это форма того промпта, а не норма пункта: DR-промпт в 12 строк с
адресацией «Тема:» и «Вопросы исследования:» проходил мимо, и наблюдено было
2 находки против вилки 6–10. Лечится не порогом (порог под корпус не
подгоняется), а вторым проходом по инварианту.

Инвариант правила prompt-kit («Промпт как копируемый артефакт», область
расширена 2026-08-25 на любой промпт, покидающий чат). Блок считается
копируемым промптом, когда сходятся три признака:

  (а) блок не является набором команд владельцу. Исключается shell-язык в
      ограде (`shell_langs`) и блок без метки языка, где доля строк-команд
      среди значимых не меньше `prompt_command_share`. Признак `is_shell`
      из `common.parse_blocks` здесь **не** используется намеренно: он метит
      блок по маркеру «Handoff for Claude Code» в строках выше, а промпт для
      Claude Code — ровно тот случай, который правило и покрывает.

  (б) блок адресован исполнителю или сессии, а не владельцу: структура задания
      любой формы — зачин строки («Задача:», «Тема:», «Вопросы исследования:»,
      «Формат выхода:», «Context»), императив, обращение к агенту, слот
      шаблона. Блок, выданный владельцу для записи в вольт (маркер
      `prompt_owner_markers` выше ограды), промптом не считается: он несёт даты
      и «реестр §B», но исполнителю не адресован, а запись в вольт делает
      владелец (CLAUDE.md, «в вольт не писать»).

  (в) блок несёт хотя бы один фактический параметр: путь, URL, идентификатор,
      число с единицей, дату, имя файла либо ветку. Это и есть механическая
      мера «Допустимых попаданий» пункта: короткий go-ahead шаблона 4 и
      корректирующая реплика фактических параметров не несут, и проверять там
      нечего — самооценка ловит правдоподобный путь или URL, а не вежливость.

**Длина блока признаком не является.** Порога `prompt_min_lines` в манифесте
больше нет: мёртвая данная читалась бы как действующий порог.

Чистые функции: ни сети, ни LLM, ни файловых эффектов.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .common import parse_blocks, significant_chars

# (б) Структура задания. Зачины разделов промпта, императив, обращение к агенту,
# слот шаблона. Список — данные манифеста (`prompt_address_patterns`).
DEFAULT_ADDRESS = [
    r"^\s*[-*>]?\s*\**\s*(?:Задача|Тема|Вопросы\s+исследования|Вопросы|Контекст|"
    r"Context|Принимай\s+как\s+данность|Формат\s+выхода|Output\s+format|"
    r"Ограничения|Constraints|Роль|Role|Task|Твоя\s+задача|Your\s+task)"
    r"\s*\**\s*[:—–-]",
    r"\bТво[яё]\s+задача\b",
    r"\bYour\s+task\b",
    r"(?<![\w~])В\s+~/",
    # Императив в начале строки: поручение исполнителю.
    r"^\s*(?:\d+[.)]\s*|[-*+•]\s*)?(?:Собери|Собрать|Найди|Найти|Дай|Дать|"
    r"Проверь|Проверить|Верни|Вернуть|Сделай|Сделать|Прочитай|Прочитать|"
    r"Составь|Составить|Изучи|Изучить|Сравни|Сравнить|Опиши|Описать|"
    r"Не\s+давай|Не\s+начинай|Не\s+пиши|Не\s+расширяй)\b",
    # Обращение к агенту.
    r"^\s*(?:Ты\b|You\s+are\b|Действуй\s+как\b|Act\s+as\b)",
    # Слот шаблона, не снятый оркестратором: `- [ADR/PDR ids + one line each]`.
    r"\[[A-Za-zА-Яа-яЁё][^\]\n]{3,}\]",
]

# (в) Фактические параметры. Их наличие отличает промпт от go-ahead.
DEFAULT_PARAMETERS = [
    r"https?://\S+",
    r"(?:^|[\s(«\"'`])(?:~|\.{1,2})?/[\w.@-]+(?:/[\w.@-]+)+",
    r"\b(?:ADR|PDR|EMV-DL)[-\s]?\d+",
    r"\bR-[A-ZА-ЯЁ]+-\d+\b",
    r"(?<![\w/])[0-9a-f]{7,40}(?![\w/])",
    r"#\d+\b",
    r"\b\d+\s*(?:строк\w*|файл\w*|тест\w*|коммит\w*|чекер\w*|сценар\w*|правил\w*|"
    r"фикстур\w*|наход\w*|%|сек\w*|мс\b|ms\b|мин\w*|min\b|ч\b|GB|MB|KB)",
    r"\b\d{4}-\d{2}-\d{2}\b",
    r"\b[\w-]+\.(?:md|ya?ml|py|json|toml|txt|sh|html|css|js|ts)\b",
    r"\b(?:main|master|chore|feat|fix|release)/[\w./-]+",
    r"\bветк\w*\s+\S+",
]

# (а) Строка-команда: набор таких строк есть блок владельцу, а не промпт агенту.
DEFAULT_COMMAND_LINE = [
    r"^\s*(?:\$\s+)?(?:\w+=\S*\s+)*(?:git|gh|python3?|\.?/?\.venv/bin/\S+|pip3?|"
    r"curl|cd|ls|cat|grep|rg|export|set|echo|printf|mkdir|rm|cp|mv|source|test|"
    r"pytest|pre-commit|docker|kubectl|gcloud|adb|npm|make|sed|awk|jq|wc|find)\b",
]
DEFAULT_COMMAND_SHARE = 0.5

# Блок, выданный владельцу для записи в вольт: не промпт исполнителю.
DEFAULT_OWNER_MARKERS = [
    r"\b(?:в|для)\s+вольт\w*",
    r"\bзапис\w+\s+(?:в|для)\s+(?:вольт\w*|реестр\w*|лог\w*|ADR)",
    r"\bстрок\w+\s+лога\b",
    r"\bблок\w*\s+владельцу\b",
]
DEFAULT_OWNER_LOOKBACK = 3
# Строка блока «значима», если несёт хоть сколько-то букв или цифр: пустые и
# декоративные строки в долю команд не входят — иначе блок из двух команд и
# десяти пустых строк перестал бы читаться как набор команд.
MIN_SIGNIFICANT = 2


@dataclass(frozen=True)
class Evidence:
    """Чем блок опознан промптом: это печатает сообщение чекера."""
    address: str
    parameter: str


def _compile(patterns) -> list[re.Pattern]:
    return [re.compile(p, re.IGNORECASE | re.MULTILINE) for p in patterns]


def is_command_set(lines: list[str], config: dict) -> bool:
    """Блок — набор команд владельцу, а не промпт агенту.

    Мера — доля строк-команд среди значимых, а не наличие одной команды: промпт
    вправе цитировать команду («прогнать `run.py --fast`»), и одна такая строка
    не делает его блоком для оболочки.
    """
    patterns = _compile(config.get("prompt_command_line_patterns")
                        or DEFAULT_COMMAND_LINE)
    share = float(config.get("prompt_command_share", DEFAULT_COMMAND_SHARE))
    meaningful = [ln for ln in lines if significant_chars(ln) >= MIN_SIGNIFICANT]
    if not meaningful:
        return False
    commands = sum(1 for ln in meaningful if any(p.search(ln) for p in patterns))
    return commands / len(meaningful) >= share


def address_hit(body: str, config: dict) -> str | None:
    """Первая форма адресации исполнителю или сессии; иначе None."""
    for pattern in _compile(config.get("prompt_address_patterns")
                            or DEFAULT_ADDRESS):
        m = pattern.search(body)
        if m:
            return " ".join(m.group(0).split())[:60]
    return None


def has_parameter(body: str, config: dict) -> str | None:
    """Первый фактический параметр блока; иначе None."""
    for pattern in _compile(config.get("prompt_parameter_patterns")
                            or DEFAULT_PARAMETERS):
        m = pattern.search(body)
        if m:
            return m.group(0).strip()[:60]
    return None


def owner_addressed(text_lines: list[str], block, config: dict) -> bool:
    """Блок выдан владельцу (запись в вольт, реестр, лог), а не исполнителю."""
    markers = _compile(config.get("prompt_owner_markers") or DEFAULT_OWNER_MARKERS)
    lookback = int(config.get("prompt_owner_lookback", DEFAULT_OWNER_LOOKBACK))
    lo = max(0, block.fence_line - 1 - lookback)
    near = " ".join(text_lines[lo:block.fence_line - 1])
    return any(m.search(near) for m in markers)


def prompt_blocks(text: str, config: dict) -> list[tuple[object, Evidence]]:
    """Огороженные блоки, читаемые как копируемый промпт другому агенту.

    Возвращает пары (блок, свидетельство): по свидетельству владелец видит, чем
    блок опознан промптом, — «адресация: “Тема:”, параметр: “2026-09-04”».
    """
    config = config or {}
    shell_langs = set(config.get("shell_langs") or [])
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")

    out: list[tuple[object, Evidence]] = []
    for b in parse_blocks(text, config):
        if b.lang in shell_langs:
            continue
        if not b.lang and is_command_set(b.lines, config):
            continue
        if owner_addressed(lines, b, config):
            continue
        body = "\n".join(b.lines)
        said = address_hit(body, config)
        if said is None:
            continue
        param = has_parameter(body, config)
        if param is None:
            continue
        out.append((b, Evidence(address=said, parameter=param)))
    return out
