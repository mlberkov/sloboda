"""S-02 / К1: утверждение оркестратора с числом или адресом канона без провенанса.

Опора: контракт §11, «Провенанс утверждений оркестратора» (реестр: R-PROV-001).

История имени. Чекер заведён 2026-08-31 как `stop_provenance` по открытому долгу
§B реестра правок канона (рецидив 2, 2026-08-27: `AHEAD_OF_MAIN: 7` — число
посчитано в уме и выдано как стоп-условие, верное значение 6) и мерил ровно одну
форму — числовой литерал в блоке стоп-условия. В тот же день по тому же правилу
записаны рецидивы 3 и 4:

  * оценка «1–5 правок канона в неделю» введена оркестратором как рамка и затем
    процитирована как основание выбора дефолта enforcement; пометки «не
    проверено» не было, измерения за оценкой нет (§B, 2026-08-31, счёт 3);
  * адрес пункта «Действия владельца — отдельным блоком» назван как §11
    контракта, тогда как пункт стоит в §7, строка 113; ошибка воспроизведена и в
    логе вольта (§B, 2026-08-31, счёт 4).

Обе формы стоят вне стоп-условий, и закрытая ранее мера их не покрывала. Реестр
правок канона §A (2026-08-31, К1) записал лечение структурной мерой: «расширение
чекера `stop_provenance` до `claim_provenance`: числовая оценка либо ссылка на
пункт канона (§, файл, строка) без соседнего провенанса или пометки — красный.
Доработка существующей механики, текст правила не трогается». По лестнице
лечения §B третий рецидив дописыванием текста не лечится вовсе — только
механикой, не зависящей от памяти оркестратора. Этот модуль и есть та механика.

Три ноги, и первая — прежняя, дословно:

1. **Стоп-условие.** Числовой литерал в блоке стоп-условия (`block_markers`) без
   соседнего провенанса. Поведение сохранено без изменений: те же маркеры, тот
   же `provenance_window`, тот же список изъятий, то же сообщение. Расширение не
   вправе переопределять закрытый долг — иначе прогон перестал бы говорить о том,
   ради чего чекер заведён.

2. **Числовая оценка в утверждении.** Формы `estimate_patterns`: «примерно N»,
   «N в неделю», «N из M». Оправдывают команда, вернувшая число, цитата выдачи
   либо пометка неизмеренности (`claim_unverified_pattern`). Слово «оценка» само
   по себе **не** оправдывает: рецидив 3 в том и состоял, что оценка была
   названа оценкой и тут же процитирована как основание. Этим список оправданий
   второй ноги отличается от `awaiting_pattern` первой, где «оценка» стоит рядом
   с «ожиданием» и читается как отказ от притязания.

3. **Адрес пункта канона.** Формы `canon_ref_patterns`: § раздела, путь внутри
   вольта, `файл.md:строка`, «строка N». Оправдывают те же три вещи — команда,
   выдача, пометка. Адрес — такое же утверждение о внешнем предмете, как число:
   §11 вместо §7 неотличим от прочитанного, пока рядом не сказано, чем читали.

Ноги 2 и 3 меряют прозу хода: строки внутри огороженных блоков не
рассматриваются (команда и её выдача — не утверждение оркестратора), и строки,
уже разобранные первой ногой, второй раз не судятся.

Списки форм и окна — данные манифеста: расширяются без правки модуля.

Чистая функция: ни сети, ни LLM, ни файловых эффектов.
"""

from __future__ import annotations

import re

from ..common import RED, Finding, in_block_lines, mask_spans, split_lines

NAME = "claim_provenance"

DEFAULT_MARKERS = [
    r"стоп-услов",
    r"стоп услов",
    r"не должно двигаться",
    r"должно совпасть",
    r"stop[- ]condition",
]
DEFAULT_IGNORE = [
    r"§\s*\d+(\.\d+)*",
    r"\b\d{4}-\d{2}-\d{2}\b",
    r"\b(ADR|PDR|EMV-DL|S|К|L)-?\d+\b",
    r"\bp?\.?\s*\d+\s*(строк|строки|строка)\b",
    r"\bv\d+(\.\d+)*\b",
    r"\bПРИМЕР\b",
]
DEFAULT_COMMAND = (
    r"`[^`]*\b(git|gh|wc|grep|rg|sed|awk|jq|curl|ls|cat|find|gcloud|adb|docker|"
    r"kubectl|python3?|pip|npm|test)\b[^`]*`"
)
DEFAULT_AWAITING = r"(ожидан|ожидается|не проверено|не измерено|оценк|прогноз|предполож)"
DEFAULT_OUTPUT = r"(выдач|вывод|измерено|провенанс|из выдачи|вернул|отдал|показал)"

# ── ноги 2 и 3 ───────────────────────────────────────────────────────────
DEFAULT_ESTIMATES = [
    r"(?:примерно|приблизительно|около|порядка|~)\s*\d+",
    r"\b\d+(?:\s*[–—-]\s*\d+)?\s+(?:\S+\s+){0,2}?в\s+"
    r"(?:неделю|день|сутки|месяц|год|ход|прогон|milestone)\b",
    r"\b\d+\s+из\s+\d+\b",
]
DEFAULT_CANON_REFS = [
    r"§\s*\d+(?:\.\d+)*",
    r"\b(?:00-system|01-theygrow|02-synthesis|03-library|04-decisions|05-inbox)"
    r"/[\w./-]+",
    r"\b[\w.-]+\.md:\d+",
    r"\b(?:строк\w+|line)\s+\d+\b",
]
# Чтение, названное словом: чем именно предмет прочитан в этом ходе.
DEFAULT_READ = (
    r"(прочитан|читан|чтени|из выдачи|выдач|вывод|измерен|провенанс|вернул|"
    r"отдал|показал|по месту|сверен|свеж\w+ чтени)"
)
# Отказ от притязания. «Оценка» сюда не входит намеренно — см. рецидив 3.
DEFAULT_UNVERIFIED = (
    r"(не\s+проверено|не\s+измерено|не\s+измеряли|не\s+сверял\w*|не\s+читал\w*|"
    r"прогноз|гипотез\w*|предполож\w*|не\s+измерение)"
)

NUMBER = re.compile(r"(?<![\w.])\d+(?![\w.])")
LIST_ITEM = re.compile(r"^\s*([-*+]|\d+[.)])\s+")


def _blocks(lines: list[str], markers: list[re.Pattern], max_lines: int):
    """Диапазоны (start_idx, end_idx) блоков стоп-условия, 0-индексные, включительно."""
    out = []
    i = 0
    while i < len(lines):
        if any(m.search(lines[i]) for m in markers):
            j = i
            blank_run = 0
            while j + 1 < len(lines) and (j - i) < max_lines:
                nxt = lines[j + 1]
                if not nxt.strip():
                    blank_run += 1
                    if blank_run > 1:
                        break
                    # пустая строка терпима, если дальше продолжается список
                    k = j + 2
                    if k >= len(lines) or not LIST_ITEM.match(lines[k]):
                        break
                    j += 1
                    continue
                if re.match(r"^#{1,6}\s", nxt):
                    break
                blank_run = 0
                j += 1
            out.append((i, j))
            i = j + 1
        else:
            i += 1
    return out


def _compile(patterns) -> list[re.Pattern]:
    return [re.compile(p, re.IGNORECASE) for p in patterns]


def check(text: str, config: dict) -> list[Finding]:
    config = config or {}
    markers = [re.compile(p, re.IGNORECASE)
               for p in (config.get("block_markers") or DEFAULT_MARKERS)]
    ignore = config.get("ignore_patterns") or DEFAULT_IGNORE
    command = re.compile(config.get("command_pattern", DEFAULT_COMMAND), re.IGNORECASE)
    awaiting = re.compile(config.get("awaiting_pattern", DEFAULT_AWAITING), re.IGNORECASE)
    output = re.compile(config.get("output_pattern", DEFAULT_OUTPUT), re.IGNORECASE)
    window = int(config.get("provenance_window", 1))
    max_lines = int(config.get("block_max_lines", 20))

    estimates = _compile(config.get("estimate_patterns") or DEFAULT_ESTIMATES)
    canon_refs = _compile(config.get("canon_ref_patterns") or DEFAULT_CANON_REFS)
    read = re.compile(config.get("read_pattern", DEFAULT_READ), re.IGNORECASE)
    unverified = re.compile(config.get("claim_unverified_pattern", DEFAULT_UNVERIFIED),
                            re.IGNORECASE)
    claim_window = int(config.get("claim_window", 1))

    lines = split_lines(text)
    findings: list[Finding] = []

    # ── нога 1: числа в блоках стоп-условия (поведение сохранено) ─────────
    stop_lines: set[int] = set()
    for start, end in _blocks(lines, markers, max_lines):
        stop_lines.update(range(start, end + 1))
        for idx in range(start, end + 1):
            masked = mask_spans(lines[idx], ignore)
            nums = list(NUMBER.finditer(masked))
            if not nums:
                continue
            lo, hi = max(start, idx - window), min(end, idx + window)
            near = "\n".join(lines[lo:hi + 1])
            if command.search(near) or awaiting.search(near) or output.search(near):
                continue
            findings.append(Finding(
                idx + 1, NAME, RED,
                f"число {nums[0].group(0)} в блоке стоп-условия без соседнего провенанса: "
                f"рядом нет ни команды, его вернувшей, ни цитаты выдачи, "
                f"ни пометки «ожидание»/«не проверено»"))

    # ── ноги 2 и 3: проза хода ───────────────────────────────────────────
    blocked = in_block_lines(text, config)
    for idx, line in enumerate(lines):
        if idx in stop_lines or (idx + 1) in blocked:
            continue
        est = next((e.search(line) for e in estimates if e.search(line)), None)
        ref = None if est else next(
            (c.search(line) for c in canon_refs if c.search(line)), None)
        hit = est or ref
        if hit is None:
            continue
        lo, hi = max(0, idx - claim_window), min(len(lines) - 1, idx + claim_window)
        near = "\n".join(lines[lo:hi + 1])
        if command.search(near) or read.search(near) or unverified.search(near):
            continue
        if est is not None:
            findings.append(Finding(
                idx + 1, NAME, RED,
                f"числовая оценка «{est.group(0).strip()}» в утверждении оркестратора "
                f"без соседнего провенанса: рядом нет ни команды, её вернувшей, ни "
                f"цитаты выдачи, ни пометки «не проверено» — рамка, введённая "
                f"оркестратором, читается владельцем как измерение и на следующем "
                f"ходе цитируется как основание"))
        else:
            findings.append(Finding(
                idx + 1, NAME, RED,
                f"адрес пункта канона «{ref.group(0).strip()}» без соседнего "
                f"провенанса чтения и без пометки «не проверено»: адрес — такое же "
                f"утверждение о внешнем предмете, как число, и §11 вместо §7 "
                f"неотличим от прочитанного, пока рядом не сказано, чем читали"))

    findings.sort(key=lambda f: f.line)
    return findings
