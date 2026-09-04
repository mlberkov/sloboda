#!/usr/bin/env python3
"""Прогон хода-линтера полосы «Система».

  run.py --fast <файлы…>   все чекеры по переданным артефактам
  run.py --full            то же по фикстурам + мета-тест + пересчёт хэшей реестра
  run.py … --kind spec     вид артефакта: handoff (умолчание) либо spec

Вид артефакта отбирает чекеры по полю kinds манифеста: чекер, которому в
задании нечего мерить, на нём и не говорит.

Находка изымается маркером `<!-- lint:ignore <checker> — причина -->` на
предыдущей строке; изъятие без причины само даёт находку (linter/ignores.py).

Чекер с непустым `config.gates` — гейт целостности входа: он прогоняется первым,
и на его срабатывании названные в `gates` чекеры не запускаются вовсе. Их
результат в отчёте — «не измерено», а не «ноль находок»: на деградировавшем
входе у них нет предмета, и ноль находок был бы пустой выдачей при нулевом коде
выхода (§11, R-VACUUM-007 — правило применено к самому линтеру).

Прогон fail-closed (несущий документ, слой 0, п. 5: отказ контролёра = отказ хода,
никогда разрешение). Исключение внутри чекера — типизированный красный
`checker_error` с именем чекера, артефактом и кадром traceback внутри linter/;
превышение `limits.checker_timeout_seconds` (CPU-время `time.process_time()`
вокруг единственной точки вызова `check()`) — красный `checker_timeout`. Оба —
класс infra, чекер на этом артефакте помечается «не измерено».

Калибровка (red-фикстура даёт ≥1 находку своего чекера, green — 0) идёт на
каждом прогоне, включая `--fast`, до целевых артефактов: чекер, проваливший её,
на этом прогоне дисквалифицирован — его находки на целях в вердикт не входят,
и по каждой цели он стоит в «Не измерено» с причиной. Иначе чекер, потерявший
способность краснеть, голосовал бы нулём находок.

Выход ≠ 0 при любом красном. Отчёт — reports/run-<UTC-timestamp>.md и .json.
"""

from __future__ import annotations

import argparse
import datetime as dt
import importlib
import json
import os
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass

import yaml

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from linter import canon                                    # noqa: E402
from linter import ignores                                  # noqa: E402
from linter.common import FAILING, Finding                  # noqa: E402

# Виды артефактов. Чекер объявляет свои в manifest.yaml (kinds).
KINDS = ("handoff", "spec")

# Класс отказа: не вердикт чекера о предмете, а отказ опоры прогона.
INFRA = "infra"

# Итог сработавшего гейта целостности входа (linter/checkers/artifact_integrity.py).
GATE_STATUS = "artifact_degraded"

# Типизированные отказы механики прогона. Все — класс infra: отказала опора,
# а не предмет под чекером. Каждый валит прогон: отказ контролёра не разрешение.
CHECKER_ERROR = "checker_error"
CHECKER_TIMEOUT = "checker_timeout"
CHECKER_IMPORT_ERROR = "checker_import_error"
ARTIFACT_UNREADABLE = "artifact_unreadable"
NO_CHECKERS_FOR_KIND = "no_checkers_for_kind"
CONFIG_INVALID_LIMIT = "config_invalid_limit"

# Ключ лимита времени на вызов чекера. Умолчания в коде нет намеренно: значение
# живёт в config.yaml, и его отсутствие — отказ конфигурации, а не «без лимита».
# Ноль молча снял бы гард, поэтому он тоже отказ.
LIMIT_KEY = "checker_timeout_seconds"

# Сообщение отказа обрезается: свидетельство должно поместиться в строку сводки,
# которую читает владелец, а не вытеснить её собой.
EVIDENCE_CHARS = 200


# ─────────────────────────────── загрузка ────────────────────────────────

def load_yaml(path: str):
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def rel(path: str) -> str:
    return os.path.join(ROOT, path)


def load_config() -> dict:
    return load_yaml(rel("config.yaml")) or {}


def load_checkers(manifest: dict, kind: str):
    """Чекеры манифеста, отобранные по виду артефакта.

    Возвращает (активные, пропущенные, отказы импорта). Чекер без поля kinds
    работает на всех видах: умолчание не должно молча выключать чекер.

    Модуль, который не импортируется, — не «чекер без находок», а отказ опоры:
    он возвращается третьим списком и красит прогон. Молча пропущенный импорт
    дал бы ноль находок при нулевом коде выхода.
    """
    shared = manifest.get("shared") or {}
    active, skipped, failed = [], [], []
    for entry in manifest.get("checkers") or []:
        kinds = list(entry.get("kinds") or KINDS)
        if kind not in kinds:
            skipped.append({"name": entry["name"], "kinds": kinds})
            continue
        try:
            mod = importlib.import_module(entry["module"])
        except Exception as exc:                       # noqa: BLE001 — отказ опоры
            failed.append({"name": entry["name"], "module": entry["module"],
                           "exc_type": type(exc).__name__,
                           "message": str(exc)[:EVIDENCE_CHARS]})
            continue
        cfg = dict(shared)
        cfg.update(entry.get("config") or {})
        active.append({"name": entry["name"], "module": mod, "config": cfg,
                       "kinds": kinds, "meta": entry})
    return active, skipped, failed


# ────────────────────── единственная точка вызова чекера ─────────────────────

@dataclass(frozen=True)
class CheckerFailure:
    """Отказ чекера на артефакте: не вердикт о предмете, а отказ контролёра."""
    checker: str
    file: str
    status: str                 # CHECKER_ERROR | CHECKER_TIMEOUT
    exc_type: str | None
    message: str
    frame: str | None
    elapsed: float | None
    limit: float | None

    def reason(self) -> str:
        """Причина для колонки «не измерено»."""
        if self.status == CHECKER_TIMEOUT:
            return f"лимит {self.limit}s"
        return f"отказ чекера: {self.exc_type}"

    def line(self) -> str:
        if self.status == CHECKER_TIMEOUT:
            return (f"[{INFRA}] {CHECKER_TIMEOUT}: {self.checker} на {self.file}: "
                    f"{self.elapsed:.3f}s при лимите {self.limit}s — вердикт чекера "
                    f"на этом артефакте не принят, чекер помечен «не измерено»")
        return (f"[{INFRA}] {CHECKER_ERROR}: {self.checker} на {self.file}: "
                f"{self.exc_type}: {self.message}"
                + (f" ({self.frame})" if self.frame else "")
                + " — отказ контролёра есть отказ хода, а не разрешение: "
                  "чекер помечен «не измерено»")


def linter_frame(exc: BaseException) -> str | None:
    """Последний кадр traceback внутри linter/ как `path:line in func`.

    Владельцу нужен адрес в механике полосы, а не в стандартной библиотеке:
    кадр `re.py` ничего не говорит о том, какой чекер и на чём упал. Кадра
    внутри linter/ нет — берётся последний кадр целиком, чтобы отказ не остался
    без адреса вовсе.
    """
    frames = traceback.extract_tb(exc.__traceback__)
    if not frames:
        return None
    inside = [f for f in frames if f"{os.sep}linter{os.sep}" in f.filename]
    frame = (inside or frames)[-1]
    return f"{repo_rel(frame.filename)}:{frame.lineno} in {frame.name}"


def checker_limit(config: dict) -> tuple[float | None, str | None]:
    """Лимит CPU-времени на вызов чекера из config.yaml.

    Возвращает (лимит, сообщение отказа). Умолчания в коде нет: значение живёт
    в конфиге, и его отсутствие — отказ конфигурации, а не «без лимита». Ноль и
    отрицательное значение сняли бы гард молча, поэтому тоже отказ.

    Меряется CPU-время процесса, а не стенные часы: чекер — чистая функция без
    I/O, и приостановленный раннер CI не должен давать ложный красный.
    """
    raw = (config.get("limits") or {}).get(LIMIT_KEY)
    if raw is None:
        return None, (f"limits.{LIMIT_KEY} в config.yaml не задан — лимит времени "
                      f"чекера не установлен; умолчания в коде нет намеренно: "
                      f"«без лимита» неотличимо от «лимит снят»")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None, (f"limits.{LIMIT_KEY} = {raw!r} — не число: лимит времени "
                      f"чекера не установлен")
    if value <= 0:
        return None, (f"limits.{LIMIT_KEY} = {raw!r} — не положительное число: "
                      f"нулевой лимит снял бы гард молча")
    return value, None


def run_checker(ch: dict, text: str, path: str,
                limit: float) -> tuple[list[Finding], CheckerFailure | None]:
    """Единственная точка вызова `check()`: перехват отказа и замер времени.

    Ловятся `Exception` и `SystemExit`: `sys.exit()` внутри чекера иначе завершил
    бы прогон кодом 0 — отказ контролёра стал бы разрешением. `KeyboardInterrupt`
    не ловится: прерывание владельцем не есть отказ чекера.

    Лимит меряется после возврата, по `time.process_time()`. Прерывания нет
    намеренно: единственный реалистичный отказ по времени — регэксп на уровне C,
    который сигналом не прерывается, а тест, висящий при провале, есть отсутствие
    ответа, а не отказ (§11, «Исполнимость shell-блока», (3) отличимость провала).
    Вечный вызов отсекается слоем CI (timeout-minutes у job `gate`).
    """
    started = time.process_time()
    try:
        found = ch["module"].check(text, ch["config"])
    except (Exception, SystemExit) as exc:             # noqa: BLE001 — отказ опоры
        return [], CheckerFailure(
            checker=ch["name"], file=repo_rel(path), status=CHECKER_ERROR,
            exc_type=type(exc).__name__, message=str(exc)[:EVIDENCE_CHARS] or "—",
            frame=linter_frame(exc), elapsed=None, limit=limit)
    elapsed = time.process_time() - started
    if elapsed > limit:
        return [], CheckerFailure(
            checker=ch["name"], file=repo_rel(path), status=CHECKER_TIMEOUT,
            exc_type=None, message="", frame=None, elapsed=elapsed, limit=limit)
    return list(found), None


def load_observations(config: dict) -> list[dict]:
    """Наблюдения прогона: пометка класса к находке, которая остаётся на месте.

    Наблюдение ничего не изымает и на вердикт не влияет — этим оно и отличается
    от `<!-- lint:ignore … -->` (linter/ignores.py), снимающего находку. Форма
    нужна там, где находка разобрана и признана не подгоняемой порогом: она
    остаётся красной, а отчёт несёт её класс.
    """
    out = []
    for o in (config.get("observations") or []):
        out.append({"file": o.get("file"), "line": o.get("line"),
                    "checker": o.get("checker"), "class": o.get("class"),
                    "note": (o.get("note") or "").strip(), "matched": 0})
    return out


def observe(observations: list[dict], file: str, line: int, checker: str) -> str | None:
    """Класс наблюдения для находки, если оно на неё заведено."""
    for o in observations:
        if o["file"] == file and int(o["line"]) == line and o["checker"] == checker:
            o["matched"] += 1
            return o["class"]
    return None


def load_scenarios(scen_dir: str) -> list[dict]:
    out = []
    for fn in sorted(os.listdir(scen_dir)):
        if fn.endswith((".yaml", ".yml")):
            data = load_yaml(os.path.join(scen_dir, fn))
            if data:
                data["_file"] = os.path.join(scen_dir, fn)
                out.append(data)
    return out


# ─────────────────────────────── прогон ──────────────────────────────────

def split_gates(checkers) -> tuple[list, list]:
    """Гейты (чекеры с непустым config.gates) и все остальные.

    Гейт судит не предмет, а пригодность входа к измерению, поэтому идёт первым:
    его вердикт решает, кого вообще запускать.
    """
    gates = [c for c in checkers if c["config"].get("gates")]
    return gates, [c for c in checkers if not c["config"].get("gates")]


def run_file(checkers, path: str, disqualified: dict[str, str] | None = None,
             limit: float = 1.0):
    """Находки по одному артефакту после применения маркеров изъятия.

    Гейты прогоняются первыми. Сработавший гейт гасит названные в его `gates`
    чекеры: на деградировавшем входе у них нет предмета, и их ноль находок был
    бы пустой выдачей при нулевом коде выхода — по §11 не отрицательным
    результатом, а отказом канала. Погашенный чекер не запускается вовсе, и
    отчёт несёт по нему «не измерено», а не «ноль находок».

    Гашение считается по сырым находкам гейта, до маркеров изъятия: `lint:ignore`
    снимает красный, но ограды в артефакте от этого не появляются — измерить
    погашенное всё равно нечем.

    Чекер, дисквалифицированный калибровкой этого прогона, не запускается вовсе:
    его находки ничего не доказывают, пока он не показал, что умеет краснеть и
    не кричит зря. Причина гашения стоит рядом с именем.

    Отказ чекера (исключение, лимит) не снимает вердикт с остальных: упавший
    помечается «не измерено», прочие меряют. Отказ гейта гасит названные им
    чекеры — гейт, не доказавший пригодность входа, не является разрешением.

    Возвращает (находки, число применённых изъятий, {чекер: причина},
    [CheckerFailure]).
    """
    try:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
    except (OSError, UnicodeDecodeError) as exc:
        return [], 0, {}, [CheckerFailure(
            checker="—", file=repo_rel(path), status=ARTIFACT_UNREADABLE,
            exc_type=type(exc).__name__, message=str(exc)[:EVIDENCE_CHARS],
            frame=None, elapsed=None, limit=None)]

    disqualified = dict(disqualified or {})
    gates, rest = split_gates(checkers)
    raw: list[Finding] = []
    not_measured: dict[str, str] = {}
    failures: list[CheckerFailure] = []

    for name, status in disqualified.items():
        not_measured.setdefault(name, f"калибровка: {status}")

    for ch in gates:
        if ch["name"] in disqualified:
            # Гейт не измерен — значит не измерено и то, что он охраняет.
            for name in ch["config"]["gates"]:
                not_measured.setdefault(
                    name, f"гейт {ch['name']} не измерен (калибровка)")
            continue
        got, failure = run_checker(ch, text, path, limit)
        if failure is not None:
            failures.append(failure)
            not_measured.setdefault(ch["name"], failure.reason())
            for name in ch["config"]["gates"]:
                not_measured.setdefault(
                    name, f"гейт {ch['name']} не измерен ({failure.reason()})")
            continue
        raw.extend(got)
        if got:
            for name in ch["config"]["gates"]:
                not_measured.setdefault(name, f"гейт {ch['name']}")

    for ch in rest:
        if ch["name"] in not_measured:
            continue
        got, failure = run_checker(ch, text, path, limit)
        if failure is not None:
            failures.append(failure)
            not_measured.setdefault(ch["name"], failure.reason())
            continue
        raw.extend(got)

    kept, applied = ignores.apply(text, raw)
    # Гасятся только объявленные в манифесте: гейт, назвавший чекер, которого на
    # этом виде артефакта нет, не должен молча ничего значить.
    live = {c["name"] for c in checkers}
    not_measured = {k: v for k, v in not_measured.items() if k in live}
    return [(path, f) for f in kept], applied, not_measured, failures


def repo_rel(path: str) -> str:
    """Путь относительно корня репозитория; вне репозитория — абсолютный."""
    try:
        r = os.path.relpath(os.path.abspath(path), ROOT)
    except ValueError:
        return os.path.abspath(path)
    return os.path.abspath(path) if r.startswith("..") else r


# ───────────────────── свежесть канона под прогоном ──────────────────────

# Пути вольта приходят только из окружения. Репозиторий не носит ни машины
# владельца, ни умолчания на неё: умолчание сделало бы «вольт не адресован»
# неотличимым от «вольт прочитан», а зелёный на неверном пути — ложным.
# Незаданная переменная — типизированный отказ опоры (vault_env_unset), а не
# откат на путь: канал 1 краснеет, канал 2 предупреждает (свежесть не измерена,
# а не подтверждена). `--fast` вольта не касается и работает при обеих снятых.
ENV_CLONE = "ALTREGO_VAULT_CLONE"
ENV_MASTER = "ALTREGO_VAULT_MASTER"

# Пути канона внутри вольта: правка вне них хэшей реестра не касается.
DEFAULT_CANON_PATHS = ("00-system/", "01-theygrow/operations/", "02-synthesis/")


def env_path(name: str) -> str | None:
    """Значение переменной окружения; пустая и незаданная равнозначны."""
    raw = (os.environ.get(name) or "").strip()
    return raw or None


def vault_clone() -> str | None:
    return env_path(ENV_CLONE)


def vault_master() -> str | None:
    return env_path(ENV_MASTER)


# Отказ git читать репозиторий, принадлежащий другому пользователю. Для
# /mnt/… из WSL это штатный случай, а не поломка: он называется отдельно и
# несёт лечение, иначе непрочитанная рабочая копия выглядит как прочитанная.
DUBIOUS_OWNERSHIP = "dubious ownership"


def _git(repo: str, *args: str, timeout: int = 120, strip: bool = True):
    """Читающий вызов git в названном репозитории: (код, stdout, stderr).

    strip=False обязателен для `status --porcelain`: первые два столбца строки —
    коды состояния, и у « M путь» первый из них пробел. Общий strip() съел бы
    его и сдвинул разбор пути на символ.
    """
    try:
        p = subprocess.run(["git", "-C", repo, *args],
                           capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, "", f"{type(exc).__name__}: {exc}"
    out = p.stdout.strip() if strip else p.stdout.rstrip("\n")
    return p.returncode, out, p.stderr.strip()


def _porcelain_paths(out: str) -> list[str]:
    """Пути из `git status --porcelain`; для переименования — обе стороны.

    Путь с не-ASCII или пробелами git отдаёт в кавычках с C-эскейпами; префикс
    каталога канона — ASCII, поэтому для отбора хватает снятия кавычек.
    """
    paths: list[str] = []
    for line in out.splitlines():
        if len(line) < 4:
            continue
        code, rest = line[:2], line[3:]
        parts = rest.split(" -> ") if "R" in code else [rest]
        for raw in parts:
            path = raw.strip()
            if len(path) >= 2 and path.startswith('"') and path.endswith('"'):
                path = path[1:-1]
            if path:
                paths.append(path)
    return paths


def registry_source_files(registry: dict) -> set[str]:
    """Файлы канона, на которые ссылается реестр: `rules[].source.file`.

    Набор выводится из реестра в момент прогона, а не задаётся списком в
    конфиге: список разошёлся бы с реестром молча, и сужение стало бы триггером
    по форме — ровно тем, от которого оно и уводит.
    """
    out: set[str] = set()
    for rule in (registry or {}).get("rules") or []:
        src = (rule.get("source") or {}) if isinstance(rule, dict) else {}
        path = (src.get("file") or "").strip()
        if path:
            out.add(path)
    return out


def is_bound(path: str, bound_files: set[str]) -> bool:
    """Связан ли путь `status --porcelain` с реестром.

    Прямое совпадение либо неотслеживаемый каталог `X/`: git показывает такой
    каталог одной строкой, и правка связанного файла внутри него видна только
    префиксом. Каталог, содержащий связанный файл, считается связанным —
    иначе сужение молча прятало бы правку под именем каталога.
    """
    if path in bound_files:
        return True
    if path.endswith("/"):
        return any(bf.startswith(path) for bf in bound_files)
    return False


def split_bound(paths, bound_files: set[str]) -> tuple[list[str], list[str]]:
    """(связанные, прочие). Пустой набор связанных файлов — сужать нечем:
    все пути считаются связанными, то есть поведение до сужения."""
    paths = list(paths)
    if not bound_files:
        return paths, []
    bound = [p for p in paths if is_bound(p, bound_files)]
    return bound, [p for p in paths if not is_bound(p, bound_files)]


def listing(paths: list[str], limit: int = 4) -> str:
    shown = ", ".join(paths[:limit])
    return shown + (f" и ещё {len(paths) - limit}" if len(paths) > limit else "")


def evidence(bound: list[str], unbound: list[str], bound_files: set[str]) -> str:
    """Свидетельство детектора: оба перечня раздельно и подписанно.

    Вердикт держит связанный набор, но печатаются все пути: владелец, коммитящий
    по сообщению, где названы не все грязные пути, закоммитит не всё.
    """
    if not bound_files:
        return (f"{listing(bound)}; набор связанных файлов из реестра не выведен — "
                f"сужать нечем, красным считается любой путь канона")
    parts = [f"связанные с правилами реестра: {listing(bound)}"]
    parts.append(f"прочие пути канона: {listing(unbound)}" if unbound
                 else "прочих путей канона нет")
    return "; ".join(parts)


def check_clone_remote(config: dict) -> dict:
    """Канал 1: клон вольта против своей удалённой ветки.

    Хэш, посчитанный по отставшему клону, ничего не говорит о каноне: прогон
    получил бы зелёный на устаревшем предмете. Поэтому отставание — красный,
    но класса infra: это отказ опоры прогона, а не вердикт чекера об артефакте.
    Типизированные итоги: ok | clone_behind_vault | clone_freshness_unknown |
    clone_missing | vault_env_unset. Сам вызов читающий: fetch трогает только
    remote-ссылки клона, рабочее дерево вольта не меняется.

    `config` путь клона больше не несёт (он приходит из окружения), но остаётся
    в сигнатуре: она — контракт канала. Сужение по связанным файлам реестра
    касается только канала 2, и закрытая сигнатура держит это на месте
    (tests/test_vault_scope.py::test_clone_remote_signature_takes_only_config).
    """
    raw = vault_clone()
    clone = os.path.expanduser(raw) if raw else None
    row = {"class": INFRA, "check": "clone_freshness", "clone": raw,
           "branch": None, "upstream": None, "head": None, "remote_head": None,
           "behind": None, "ahead": None, "status": "ok", "message": None}
    reds: list[str] = []
    warns: list[str] = []

    def fail(status: str, message: str) -> dict:
        row["status"], row["message"] = status, message
        reds.append(f"[{INFRA}] {status}: {message}")
        return {"row": row, "reds": reds, "warnings": warns}

    if not raw:
        return fail("vault_env_unset",
                    f"переменная окружения {ENV_CLONE} не задана — вольт-клон не "
                    f"адресован: пересчёт хэшей не на чем основывать, и умолчания "
                    f"на путь владельца в репозитории нет; владельцу задать "
                    f"{ENV_CLONE} и повторить прогон")

    if not os.path.isdir(os.path.join(clone, ".git")):
        return fail("clone_missing",
                    f"вольт-клон {raw} не найден (нет {clone}/.git) — "
                    f"пересчёт хэшей не на чем основывать")

    rc, _out, err = _git(clone, "fetch", "--quiet")
    if rc != 0:
        return fail("clone_freshness_unknown",
                    f"git -C {raw} fetch вернул код {rc} ({err.splitlines()[-1] if err else 'без stderr'}) — "
                    f"свежесть клона не измерена, а не подтверждена")

    rc, branch, _ = _git(clone, "rev-parse", "--abbrev-ref", "HEAD")
    if rc != 0:
        return fail("clone_freshness_unknown", f"не удалось определить ветку клона {raw}")
    row["branch"] = branch

    rc, upstream, err = _git(clone, "rev-parse", "--abbrev-ref",
                             "--symbolic-full-name", "@{u}")
    if rc != 0 or not upstream:
        return fail("clone_freshness_unknown",
                    f"у ветки {branch} клона {raw} нет удалённой ветки — "
                    f"сравнивать HEAD не с чем")
    row["upstream"] = upstream

    _rc, head, _ = _git(clone, "rev-parse", "HEAD")
    _rc, remote_head, _ = _git(clone, "rev-parse", upstream)
    row["head"], row["remote_head"] = head, remote_head

    rc, counts, _ = _git(clone, "rev-list", "--left-right", "--count",
                         f"HEAD...{upstream}")
    if rc != 0 or not counts:
        return fail("clone_freshness_unknown",
                    f"не удалось посчитать расхождение HEAD ↔ {upstream} в клоне {raw}")
    ahead, behind = (int(x) for x in counts.split())
    row["ahead"], row["behind"] = ahead, behind

    if behind > 0:
        return fail("clone_behind_vault",
                    f"вольт-клон {raw} отстаёт от {upstream} на {behind} коммит(ов) "
                    f"(HEAD {head[:8]} ≠ {remote_head[:8]}): хэши считались бы по "
                    f"устаревшему канону — владельцу сделать "
                    f"`git -C {raw} pull --ff-only` и повторить прогон")
    if ahead > 0:
        warns.append(f"[{INFRA}] clone_ahead_of_vault: клон {raw} впереди {upstream} "
                     f"на {ahead} коммит(ов) — клон читающий, локальные коммиты "
                     f"в нём не ожидаются")
    row["message"] = (f"{branch} ↔ {upstream}: HEAD {head[:8]}, "
                      f"удалённый {remote_head[:8]}, отставание {behind}")
    return {"row": row, "reds": reds, "warnings": warns}


def check_vault_master(config: dict, clone_raw: str, clone_head: str | None,
                       bound_files: set[str] | None = None) -> dict:
    """Канал 2: рабочая копия вольта против клона.

    Канала 1 мало: он сравнивает клон с его remote и зеленеет ровно тогда,
    когда правка канона лежит в рабочей копии вольта незакоммиченной — клон
    свеж относительно origin, а предмет, по которому считаются хэши, уже
    другой. Здесь читается сама рабочая копия: `git status --porcelain` и
    `git rev-parse HEAD`. Оба вызова читающие, в вольт ничего не пишется.

    Красным считается расхождение **по связанным файлам**: `bound_files` —
    набор `rules[].source.file`, выведенный из реестра в момент прогона. Правка
    файла канона, на который не ссылается ни одно правило (запись оркестратора
    в `00-system/log.md`), ни одного `content_hash` не двигает: она даёт
    предупреждение, а не красный. Триггер по инварианту, а не по форме пути.
    Набор пуст (реестр не прочитан либо в нём нет `source.file`) — сужать нечем,
    и красным считается любой грязный путь канона, как до сужения.

    Вердикт держит связанный набор, но свидетельство печатается целиком: оба
    перечня, связанный и прочий, называются раздельно и подписанно. Владелец,
    коммитящий по сообщению, где названы не все грязные пути, закоммитит не всё.

    Типизированные итоги: ok | vault_uncommitted | vault_uncommitted_unbound |
    vault_ahead_of_clone | vault_ahead_unbound | vault_master_unreachable |
    vault_env_unset. Недоступность пути (не WSL, диск не смонтирован, git отказал
    читать чужой по владельцу репозиторий) и незаданная переменная —
    предупреждение, а не зелёный: свежесть не измерена, а не подтверждена.
    """
    vault = config.get("vault") or {}
    raw = vault_master()
    canon_paths = tuple(vault.get("canon_paths") or DEFAULT_CANON_PATHS)
    master = os.path.expanduser(raw) if raw else None
    bound_files = set(bound_files or ())
    row = {"class": INFRA, "check": "vault_master", "master": raw,
           "canon_paths": list(canon_paths), "head": None, "clone_head": clone_head,
           "dirty_canon": None, "bound_files": sorted(bound_files),
           "dirty_bound": None, "dirty_unbound": None, "ahead": None,
           "ahead_files": None, "status": "ok", "message": None}
    reds: list[str] = []
    warns: list[str] = []

    def unmeasured(message: str, status: str = "vault_master_unreachable") -> dict:
        row["status"], row["message"] = status, message
        warns.append(f"[{INFRA}] {status}: {message}")
        return {"row": row, "reds": reds, "warnings": warns}

    def dubious(action: str) -> dict:
        return unmeasured(
            f"git отказался читать {raw} на `{action}`: dubious ownership — "
            f"репозиторий принадлежит другому пользователю (штатный случай для "
            f"/mnt/… из WSL, не поломка вольта). Лечение владельцу: "
            f"`git config --global --add safe.directory {master}`; до него "
            f"незакоммиченная правка канона прогоном не измерена")

    if not raw:
        return unmeasured(f"переменная окружения {ENV_MASTER} не задана — рабочая "
                          f"копия вольта не читалась: незакоммиченная правка канона "
                          f"прогоном не измерена, а не подтверждена",
                          status="vault_env_unset")
    if not os.path.isdir(os.path.join(master, ".git")):
        return unmeasured(f"рабочая копия вольта {raw} недоступна (нет {master}/.git) — "
                          f"не WSL либо диск не смонтирован: незакоммиченная правка "
                          f"канона прогоном не измерена")

    rc, porcelain, err = _git(master, "status", "--porcelain", strip=False)
    if rc != 0:
        if DUBIOUS_OWNERSHIP in err:
            return dubious("git status --porcelain")
        return unmeasured(f"git -C {raw} status --porcelain вернул код {rc} "
                          f"({err.splitlines()[-1] if err else 'без stderr'}) — "
                          f"состояние рабочей копии вольта не измерено")

    rc, head, err = _git(master, "rev-parse", "HEAD")
    if rc != 0 or not head:
        if DUBIOUS_OWNERSHIP in err:
            return dubious("git rev-parse HEAD")
        return unmeasured(f"git -C {raw} rev-parse HEAD вернул код {rc} "
                          f"({err.splitlines()[-1] if err else 'без stderr'}) — "
                          f"HEAD рабочей копии вольта не измерен")
    row["head"] = head

    statuses: list[str] = []

    dirty = sorted({p for p in _porcelain_paths(porcelain)
                    if any(p.startswith(c) for c in canon_paths)})
    row["dirty_canon"] = dirty
    dirty_bound, dirty_unbound = split_bound(dirty, bound_files)
    row["dirty_bound"], row["dirty_unbound"] = dirty_bound, dirty_unbound
    if dirty_bound:
        statuses.append("vault_uncommitted")
        reds.append(f"[{INFRA}] vault_uncommitted: в рабочей копии вольта {raw} "
                    f"незакоммичены изменения по путям канона "
                    f"({evidence(dirty_bound, dirty_unbound, bound_files)}) — "
                    f"клон их не видит, хэши связанных правил считались бы по канону "
                    f"без этих правок; владельцу закоммитить и запушить все "
                    f"перечисленные пути, затем "
                    f"`git -C {clone_raw} pull --ff-only` и повторить прогон")
    elif dirty_unbound:
        statuses.append("vault_uncommitted_unbound")
        warns.append(f"[{INFRA}] vault_uncommitted_unbound: в рабочей копии вольта "
                     f"{raw} незакоммичены изменения по путям канона "
                     f"({listing(dirty_unbound)}), но ни один из них не назван в "
                     f"rules[].source.file — ни один content_hash реестра от них "
                     f"не зависит, и вердикт прогона они не меняют")

    if not clone_head:
        warns.append(f"[{INFRA}] vault_ahead_unknown: HEAD клона {clone_raw} не измерен "
                     f"(см. clone_freshness выше) — сравнивать HEAD рабочей копии "
                     f"вольта не с чем")
    elif head != clone_head:
        # Сравнение делается в клоне: рабочая копия вольта только опрашивается.
        known = _git(os.path.expanduser(clone_raw), "cat-file", "-e",
                     f"{head}^{{commit}}")[0] == 0
        ahead = None
        if known:
            rc, cnt, _ = _git(os.path.expanduser(clone_raw), "rev-list", "--count",
                              f"{clone_head}..{head}")
            ahead = int(cnt) if rc == 0 and cnt.isdigit() else None
        row["ahead"] = ahead
        # Состав правки: сузить можно только по измеренному. Коммит, клону
        # неизвестный (не запушен), состава не даёт — там сужать нечем.
        changed = None
        if known:
            rc, names, _ = _git(os.path.expanduser(clone_raw), "diff", "--name-only",
                                f"{clone_head}..{head}")
            if rc == 0:
                changed = sorted({p for p in names.splitlines() if p.strip()})
        row["ahead_files"] = changed
        if ahead == 0:
            warns.append(f"[{INFRA}] vault_behind_clone: HEAD рабочей копии вольта "
                         f"{raw} ({head[:8]}) — предок HEAD клона ({clone_head[:8]}): "
                         f"клон читает канон новее, чем лежит в вольте")
        else:
            ahead_bound, ahead_unbound = split_bound(changed or [], bound_files)
            narrowed = changed is not None and bool(bound_files)
            if narrowed and not ahead_bound:
                statuses.append("vault_ahead_unbound")
                warns.append(f"[{INFRA}] vault_ahead_unbound: HEAD рабочей копии вольта "
                             f"{raw} ({head[:8]}) впереди клона ({clone_head[:8]}) на "
                             f"{ahead} коммит(ов), но правка трогает только "
                             f"({listing(ahead_unbound)}) — ни один из этих путей не "
                             f"назван в rules[].source.file, и ни один content_hash "
                             f"реестра от них не зависит")
            else:
                statuses.append("vault_ahead_of_clone")
                detail = (f"на {ahead} коммит(ов)" if ahead
                          else f"коммит {head[:8]} клону неизвестен (не запушен)")
                seen = (evidence(ahead_bound, ahead_unbound, bound_files) if changed
                        is not None else "состав правки не измерен: коммита нет в "
                        "клоне — сужать по связанным файлам нечем")
                reds.append(f"[{INFRA}] vault_ahead_of_clone: HEAD рабочей копии вольта "
                            f"{raw} ({head[:8]}) впереди клона ({clone_head[:8]}) {detail} "
                            f"({seen}) — клон читает устаревший канон; владельцу "
                            f"запушить вольт, затем "
                            f"`git -C {clone_raw} pull --ff-only` и повторить прогон")

    row["status"] = "+".join(statuses) if statuses else "ok"
    row["message"] = (f"HEAD {head[:8]}"
                      + (f" ↔ клон {clone_head[:8]}" if clone_head else "")
                      + f", незакоммиченных путей канона: {len(dirty)}"
                      + f" (связанных с реестром: {len(dirty_bound)})")
    return {"row": row, "reds": reds, "warnings": warns}


# ─────────────────────────── реестр и сценарии ───────────────────────────

ROLES = ("primary", "secondary")


def scenario_refs(s: dict) -> list[dict]:
    """Ссылки сценария на пункты канона (схема rule_refs)."""
    out = []
    for ref in s.get("rule_refs") or []:
        if isinstance(ref, dict):
            out.append(ref)
    return out


def check_registry(config: dict, registry: dict, scenarios: list[dict]) -> dict:
    # Клон не адресован — хэши не пересчитываются ни по какому пути: пустая
    # строка даёт UNRESOLVED по каждому правилу, а красный за отсутствие опоры
    # уже поставлен каналом 1 (vault_env_unset).
    clone = vault_clone() or ""
    reds: list[str] = []
    warns: list[str] = []
    rows: list[dict] = []

    # Расхождение хэшей и «мёртвая норма» считаются только по primary: secondary —
    # ссылка на соседний пункт, она не даёт сценарию права судить о его свежести
    # и не спасает пункт от статуса мёртвой нормы.
    primary: dict[str, list[tuple[dict, dict]]] = {}
    secondary: dict[str, list[tuple[dict, dict]]] = {}
    for s in scenarios:
        if s.get("status") != "active":
            continue
        refs = scenario_refs(s)
        if not refs:
            reds.append(f"{s.get('id')}: нет rule_refs — сценарий не назван ни одной нормой")
            continue
        if not any(r.get("role") == "primary" for r in refs):
            reds.append(f"{s.get('id')}: среди rule_refs нет role: primary — "
                        f"некому держать норму")
        for r in refs:
            role = r.get("role")
            if role not in ROLES:
                reds.append(f"{s.get('id')} ↔ {r.get('rule_id')}: role "
                            f"{role!r} вне {'|'.join(ROLES)}")
                continue
            (primary if role == "primary" else secondary).setdefault(
                r.get("rule_id"), []).append((s, r))

    for rule in registry.get("rules") or []:
        rid = rule["rule_id"]
        src = rule.get("source") or {}
        recomputed, section = canon.hash_rule(clone, src.get("file", ""),
                                              src.get("heading", ""))
        stored = rule.get("content_hash")
        row = {
            "rule_id": rid,
            "file": src.get("file"),
            "heading": src.get("heading"),
            "stored": stored,
            "recomputed": recomputed,
            "lines": f"{section.start_line}-{section.end_line}" if section else None,
            "scenarios": [s["id"] for s, _ in primary.get(rid, [])],
            "scenarios_secondary": [s["id"] for s, _ in secondary.get(rid, [])],
            "status": "ok",
        }

        if recomputed == canon.UNRESOLVED:
            row["status"] = "unresolved"
            warns.append(f"{rid}: пункт не найден в вольт-клоне — content_hash UNRESOLVED")
        elif stored == canon.UNRESOLVED:
            row["status"] = "registry-unresolved"
            warns.append(f"{rid}: в реестре UNRESOLVED, в вольте пункт найден "
                         f"({recomputed[:8]}) — реестр требует связывания")
        elif stored != recomputed:
            row["status"] = "registry-drift"
            reds.append(f"{rid}: registry.content_hash {str(stored)[:8]} ≠ "
                        f"вольт {recomputed[:8]} — реестр разошёлся с каноном")

        for s, ref in primary.get(rid, []):
            bound = ref.get("hash_at_binding")
            if recomputed == canon.UNRESOLVED:
                continue
            if bound != recomputed:
                row["status"] = "scenario-drift"
                reds.append(f"{s['id']} ↔ {rid} (primary): hash_at_binding "
                            f"{str(bound)[:8]} ≠ вольт {recomputed[:8]} — "
                            f"пункт канона изменился после связывания сценария")

        if not primary.get(rid):
            tail = (f"; secondary-ссылки есть "
                    f"({', '.join(s['id'] for s, _ in secondary.get(rid, []))}), "
                    f"но они норму не держат" if secondary.get(rid) else "")
            warns.append(f"{rid}: нет активного сценария с ролью primary — "
                         f"мёртвая норма{tail}")
        rows.append(row)

    known = {r["rule_id"] for r in (registry.get("rules") or [])}
    for s in scenarios:
        for ref in scenario_refs(s):
            if ref.get("rule_id") not in known:
                reds.append(f"{s.get('id')}: rule_id {ref.get('rule_id')} "
                            f"({ref.get('role')}) отсутствует в реестре")

    return {"rows": rows, "reds": reds, "warnings": warns}


# ──────────────────────────────── калибровка ─────────────────────────────

def calibrate(checkers, fixtures_dir: str, limit: float):
    """Калибровка чекеров: red-фикстура обязана дать ≥1 находку, green — 0.

    Идёт на каждом прогоне, включая `--fast`, и до целевых артефактов. Чекер,
    провал которого здесь показан, на этом прогоне дисквалифицирован: его
    находки на целях в вердикт не входят, и по каждой цели он стоит в «Не
    измерено». Иначе детектор, потерявший способность краснеть, продолжал бы
    голосовать нулём находок — пустая выдача при нулевом коде выхода.

    Отказ чекера на собственной фикстуре (исключение, лимит) — та же
    дисквалификация: «не измерен», а не «оправдан».
    """
    reds: list[str] = []
    warns: list[str] = []
    rows: list[dict] = []
    findings: list[tuple[str, Finding]] = []
    disqualified: dict[str, str] = {}
    failures: list[CheckerFailure] = []

    per_file: dict[str, list[Finding]] = {}
    # {фикстура: {погашенный чекер: причина}} — фикстура вправе быть
    # деградировавшей: это предмет S-09, а не поломка полосы.
    per_file_gated: dict[str, dict[str, str]] = {}
    # {чекер: статус отказа на своей фикстуре}
    own_failure: dict[str, str] = {}
    applied_total = 0
    for colour in ("red", "green"):
        d = os.path.join(fixtures_dir, colour)
        if not os.path.isdir(d):
            reds.append(f"нет каталога фикстур {repo_rel(d)}")
            continue
        for fn in sorted(os.listdir(d)):
            if not fn.endswith(".md"):
                continue
            path = os.path.join(d, fn)
            got, applied, gated, failed = run_file(checkers, path, None, limit)
            applied_total += applied
            findings.extend(got)
            per_file[path] = [f for _, f in got]
            per_file_gated[path] = gated
            failures.extend(failed)
            for f in failed:
                own_failure.setdefault(f.checker, f.status.replace("_", "-"))

    for ch in checkers:
        name = ch["name"]
        if name in own_failure:
            # Чекер отказал на фикстуре: измерять им цели нечем.
            disqualified[name] = own_failure[name]
            rows.append({"checker": name, "red_findings": None,
                         "green_findings": None, "status": own_failure[name],
                         "not_measured": []})
            continue
        red_path = os.path.join(fixtures_dir, "red", f"{name}.md")
        green_path = os.path.join(fixtures_dir, "green", f"{name}.md")
        row = {"checker": name, "red_findings": None, "green_findings": None,
               "status": "ok"}

        if not os.path.isfile(red_path):
            reds.append(f"{name}: нет red-фикстуры {repo_rel(red_path)}")
            row["status"] = "no-red-fixture"
        elif name in per_file_gated.get(red_path, {}):
            # Собственная red-фикстура чекера деградировала так, что гейт погасил
            # его же: «0 находок» здесь ничего не доказывает — и не должно читаться
            # как «детектор не умеет краснеть». Это дефект фикстуры, не чекера.
            gate = per_file_gated[red_path][name]
            row["red_findings"] = None
            row["status"] = "red-fixture-not-measured"
            reds.append(f"{name}: на своей red-фикстуре {repo_rel(red_path)} погашен "
                        f"гейтом {gate} — детектор не измерен, а не оправдан; "
                        f"фикстуре вернуть ограды")
        else:
            own = [f for f in per_file.get(red_path, []) if f.checker == name]
            row["red_findings"] = len(own)
            if not own:
                reds.append(f"{name}: не покраснел на своей red-фикстуре "
                            f"{repo_rel(red_path)} — детектор не умеет краснеть")
                row["status"] = "red-fixture-silent"

        if not os.path.isfile(green_path):
            reds.append(f"{name}: нет green-фикстуры {repo_rel(green_path)}")
            row["status"] = "no-green-fixture"
        elif name in per_file_gated.get(green_path, {}):
            gate = per_file_gated[green_path][name]
            row["green_findings"] = None
            row["status"] = "green-fixture-not-measured"
            reds.append(f"{name}: на своей green-фикстуре {repo_rel(green_path)} погашен "
                        f"гейтом {gate} — «ложных срабатываний нет» здесь не измерено, "
                        f"а не подтверждено; фикстуре вернуть ограды")
        else:
            own = [f for f in per_file.get(green_path, []) if f.checker == name]
            row["green_findings"] = len(own)
            if own:
                reds.append(f"{name}: покраснел на своей green-фикстуре "
                            f"{repo_rel(green_path)} ({len(own)} наход.) — ложное срабатывание")
                row["status"] = "green-fixture-red"
        row["not_measured"] = sorted(per_file_gated.get(red_path, {})
                                     | per_file_gated.get(green_path, {}))
        if row["status"] != "ok":
            disqualified[name] = row["status"]
        rows.append(row)

    # Изъятие без причины в собственных фикстурах полосы — дефект полосы, не
    # вердикт о предмете: в --full такая находка валит прогон.
    for path, fs in sorted(per_file.items()):
        for f in fs:
            if f.checker == ignores.NAME:
                reds.append(f"{repo_rel(path)}:{f.line} {f.message}")

    green_dir = os.path.join(fixtures_dir, "green")
    for path, fs in per_file.items():
        if os.path.dirname(path) != green_dir:
            continue
        own = os.path.splitext(os.path.basename(path))[0]
        for f in fs:
            if f.checker != own:
                warns.append(f"{repo_rel(path)}:{f.line} чужой чекер {f.checker} "
                             f"покраснел на green-фикстуре — {f.message}")

    # Погашенные чекеры по фикстурам — в отчёт: «не измерено» на фикстуре видно
    # так же, как на живом артефакте.
    gated_rows = [{"file": repo_rel(path), "checker": name, "gate": reason,
                   "reason": reason}
                  for path, g in sorted(per_file_gated.items())
                  for name, reason in sorted(g.items())]

    return {"rows": rows, "reds": reds, "warnings": warns, "findings": findings,
            "ignores_applied": applied_total, "not_measured": gated_rows,
            "disqualified": disqualified, "failures": failures}


# ──────────────────────────────── отчёт ──────────────────────────────────

def write_report(config: dict, payload: dict) -> tuple[str, str]:
    reports_dir = rel((config.get("paths") or {}).get("reports", "reports"))
    os.makedirs(reports_dir, exist_ok=True)
    ts = payload["timestamp"]
    # Метка времени секундная: два прогона в одну секунду не должны затирать друг друга.
    stem, n = f"run-{ts}", 1
    while os.path.exists(os.path.join(reports_dir, f"{stem}.md")) or \
            os.path.exists(os.path.join(reports_dir, f"{stem}.json")):
        n += 1
        stem = f"run-{ts}-{n}"
    md_path = os.path.join(reports_dir, f"{stem}.md")
    json_path = os.path.join(reports_dir, f"{stem}.json")

    L = [f"# Прогон линтера — {payload['mode']} — вид {payload['kind']} — {ts} UTC", ""]
    L.append(f"Артефактов проверено: {payload['files_checked']}. "
             f"Чекеров: {', '.join(payload['checkers']) or '—'}.")
    skipped = payload.get("checkers_skipped") or []
    if skipped:
        L.append(f"Не запускались на виде «{payload['kind']}»: "
                 + ", ".join(f"{c['name']} (kinds: {', '.join(c['kinds'])})"
                             for c in skipped) + ".")
    L.append("")

    L.append("## Находки")
    L.append("")
    if payload["findings"]:
        for f in payload["findings"]:
            mark = f" [наблюдение: {f['observation']}]" if f.get("observation") else ""
            L.append(f"{f['file']}:{f['line']} {f['checker']}{mark} {f['message']}")
    else:
        L.append("— пусто")
    L.append("")

    if payload.get("observations"):
        L.append("## Наблюдения")
        L.append("")
        L.append("Наблюдение не изымает находку и на вердикт не влияет: находка "
                 "остаётся красной, отчёт несёт её класс.")
        L.append("")
        L.append("| адрес | чекер | класс | совпало | наблюдение |")
        L.append("|---|---|---|---|---|")
        for o in payload["observations"]:
            L.append(f"| {o['file']}:{o['line']} | {o['checker']} | {o['class']} | "
                     f"{o['matched']} | {o['note'] or '—'} |")
        L.append("")

    if payload.get("not_measured"):
        L.append("## Не измерено")
        L.append("")
        L.append("Чекер не измерен: погашен гейтом целостности входа, "
                 "дисквалифицирован калибровкой этого прогона либо отказал "
                 "(исключение, лимит). Это «не измерено», а не «ноль находок» — "
                 "пустая выдача при нулевом коде выхода не отрицательный "
                 "результат (§11, R-VACUUM-007).")
        L.append("")
        L.append("| артефакт | чекер | причина |")
        L.append("|---|---|---|")
        for r in payload["not_measured"]:
            L.append(f"| {r['file']} | {r['checker']} | "
                     f"{r.get('reason') or r.get('gate')} |")
        L.append("")

    if payload.get("infra"):
        L.append("## Инфраструктура")
        L.append("")
        L.append("Отказ здесь — класс `infra`: отказала опора прогона, "
                 "а не предмет под чекером.")
        L.append("")
        L.append("| проверка | класс | итог | подробность |")
        L.append("|---|---|---|---|")
        for r in payload["infra"]:
            L.append(f"| {r['check']} | {r['class']} | {r['status']} | "
                     f"{r.get('message') or '—'} |")
        L.append("")

    if payload.get("meta"):
        L.append("## Калибровка чекеров")
        L.append("")
        L.append("Идёт на каждом прогоне до целевых артефактов. Чекер со статусом "
                 "не `ok` дисквалифицирован: его находки на целях в вердикт не "
                 "входят, и по каждой цели он стоит в «Не измерено».")
        L.append("")
        L.append("| чекер | red-фикстура | green-фикстура | итог |")
        L.append("|---|---|---|---|")
        for r in payload["meta"]:
            def cell(v):
                return "не измерено" if v is None else str(v)
            L.append(f"| {r['checker']} | {cell(r['red_findings'])} | "
                     f"{cell(r['green_findings'])} | {r['status']} |")
        L.append("")

    if payload.get("registry"):
        L.append("## Реестр правил ↔ вольт")
        L.append("")
        L.append("| rule_id | хэш реестра | хэш вольта | строки | сценарии primary "
                 "| сценарии secondary | итог |")
        L.append("|---|---|---|---|---|---|---|")
        for r in payload["registry"]:
            L.append(f"| {r['rule_id']} | `{str(r['stored'])[:8]}` | "
                     f"`{str(r['recomputed'])[:8]}` | {r['lines'] or '—'} | "
                     f"{', '.join(r['scenarios']) or '—'} | "
                     f"{', '.join(r.get('scenarios_secondary') or []) or '—'} | "
                     f"{r['status']} |")
        L.append("")

    L.append("## Сводка")
    L.append("")
    L.append(f"- изъятий применено: {payload['ignores_applied']}")
    L.append(f"- красных: {len(payload['reds'])}")
    for r in payload["reds"]:
        L.append(f"  - {r}")
    L.append(f"- предупреждений: {len(payload['warnings'])}")
    for w in payload["warnings"]:
        L.append(f"  - {w}")
    L.append(f"- вердикт: **{payload['verdict']}** (код выхода {payload['exit_code']})")
    L.append("")

    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L))
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    return md_path, json_path


# ──────────────────────────────── main ───────────────────────────────────

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Хода-линтер полосы «Система», v0")
    g = ap.add_mutually_exclusive_group(required=True)
    # nargs="*" + хвостовой позиционный список: иначе `--fast --kind spec ФАЙЛ`
    # падает — argparse читает `--kind` как опцию и объявляет --fast без значений.
    g.add_argument("--fast", nargs="*", metavar="ФАЙЛ",
                   help="все чекеры по переданным артефактам")
    g.add_argument("--full", action="store_true",
                   help="фикстуры + мета-тест + пересчёт хэшей реестра")
    ap.add_argument("files", nargs="*", metavar="ФАЙЛ",
                    help="артефакты (равнозначно перечислению после --fast)")
    ap.add_argument("--kind", choices=KINDS, default="handoff",
                    help="вид артефакта: чекеры отбираются по полю kinds манифеста "
                         "(по умолчанию handoff)")
    ap.add_argument("--no-report", action="store_true",
                    help="не писать файл отчёта (отчёт только в stdout)")
    args = ap.parse_args(argv)

    targets = list(args.fast or []) + list(args.files or [])
    if args.full and targets:
        ap.error("--full не принимает список файлов: он ходит по фикстурам")
    if args.fast is not None and not targets:
        ap.error("--fast требует хотя бы один артефакт")

    config = load_config()
    paths = config.get("paths") or {}
    manifest = load_yaml(rel(paths.get("manifest", "linter/manifest.yaml"))) or {}
    checkers, skipped, import_failed = load_checkers(manifest, args.kind)
    observations = load_observations(config)

    ts = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    findings: list[tuple[str, Finding]] = []
    reds: list[str] = []
    warnings: list[str] = []
    meta_rows = None
    reg_rows = None
    infra_rows: list[dict] = []
    not_measured: list[dict] = []
    ignores_applied = 0
    files_checked = 0

    def infra_fail(check: str, status: str, message: str, **extra) -> None:
        """Отказ опоры: строка таблицы и красный ставятся одной точкой.

        Две раздельные записи — канал утечки нулевого кода выхода: строка в
        таблице без строки в `reds` даёт зелёный вердикт при отказавшем контроле.
        """
        row = {"class": INFRA, "check": check, "status": status,
               "message": message}
        row.update(extra)
        infra_rows.append(row)
        reds.append(f"[{INFRA}] {status}: {message}")

    for failed in import_failed:
        infra_fail(failed["name"], CHECKER_IMPORT_ERROR,
                   f"{failed['name']} ({failed['module']}): {failed['exc_type']}: "
                   f"{failed['message']} — модуль чекера не импортирован: его ноль "
                   f"находок был бы пустой выдачей при нулевом коде выхода")

    # Лимит времени на вызов чекера. Его отсутствие — отказ конфигурации, а не
    # «без лимита»: прогон без лимита не отличил бы зависший чекер от чистого.
    limit, limit_problem = checker_limit(config)
    if limit_problem is not None:
        infra_fail("limits", CONFIG_INVALID_LIMIT, limit_problem)
        limit = float("inf")            # прогон уже красный; гард ниже не мешает

    fixtures = rel(paths.get("fixtures", "linter/fixtures"))
    # Калибровка идёт до целей и в обоих режимах: чекер, не показавший, что умеет
    # краснеть и не кричит зря, на этом прогоне не голосует. Набор — тот же, что
    # у вида цели (`--kind`): калибруется ровно то, чем меряются цели.
    mt = calibrate(checkers, fixtures, limit)
    meta_rows = mt["rows"]
    disqualified = mt["disqualified"]
    reds.extend(mt["reds"])
    warnings.extend(mt["warnings"])
    for failure in mt["failures"]:
        # Отказ чекера на собственной фикстуре: строка таблицы и красный вместе.
        infra_fail(failure.checker, failure.status,
                   failure.line().split(": ", 1)[1], file=failure.file)

    if args.fast is not None:
        if not checkers:
            infra_fail(args.kind, NO_CHECKERS_FOR_KIND,
                       f"вид «{args.kind}»: в манифесте ни один чекер этот вид не "
                       f"объявляет — ноль чекеров дал бы ноль находок при нулевом "
                       f"коде выхода, то есть зелёный на неизмеренном")
        for path in targets:
            if not os.path.isfile(path):
                reds.append(f"нет файла: {path}")
                continue
            files_checked += 1
            got, applied, gated, failed = run_file(checkers, path, disqualified,
                                                   limit)
            ignores_applied += applied
            findings.extend(got)
            for failure in failed:
                infra_fail(failure.checker, failure.status, failure.line()
                           .split(": ", 1)[1], file=failure.file)
            for name, reason in sorted(gated.items()):
                not_measured.append({"file": repo_rel(path), "checker": name,
                                     "gate": reason, "reason": reason})
            by_gate: dict[str, list[str]] = {}
            for name, reason in gated.items():
                if reason.startswith("гейт ") and " не измерен" not in reason:
                    by_gate.setdefault(reason.split("гейт ", 1)[1], []).append(name)
            for gate, names in sorted(by_gate.items()):
                # Отказала опора прогона, а не предмет под чекером: класс infra.
                # Строка стоит рядом со свежестью клона — там же, где полоса
                # называет всё, что не измерено, а не подтверждено.
                infra_fail(gate, GATE_STATUS,
                           f"{repo_rel(path)}: вход деградировал (гейт {gate}) — "
                           f"не измерены: {', '.join(sorted(names))}",
                           file=repo_rel(path), not_measured=sorted(names))
        # В сводке — только адрес находки: сообщение уже стоит в списке выше.
        for path, f in findings:
            addr = f"{repo_rel(path)}:{f.line} {f.checker}"
            (reds if f.severity in FAILING else warnings).append(addr)
        mode = "fast"
    else:
        findings = mt["findings"]
        files_checked = sum(
            1 for c in ("red", "green")
            if os.path.isdir(os.path.join(fixtures, c))
            for f in os.listdir(os.path.join(fixtures, c)) if f.endswith(".md"))
        # На фикстурах гейт строки infra не даёт: фикстура — инструмент полосы,
        # а не артефакт под измерением, и её деградация есть предмет S-09.
        # Судит её калибровка: погашенный на собственной фикстуре чекер там красный.
        not_measured.extend(mt["not_measured"])
        ignores_applied += mt["ignores_applied"]

        # Свежесть клона — предпосылка пересчёта хэшей: меряется до него.
        # Каналов два, и одного первого мало: канал 1 (клон ↔ его remote)
        # зеленеет ровно тогда, когда правка канона лежит в рабочей копии вольта
        # незакоммиченной, поэтому канал 2 (рабочая копия ↔ клон) читает саму
        # рабочую копию. Реестр грузится до них: канал 2 сужает красный по
        # `rules[].source.file`, и набор связанных файлов выводится из того же
        # объекта реестра, по которому дальше идёт пересчёт хэшей.
        registry = load_yaml(rel(paths.get("registry", "rules/registry.yaml"))) or {}
        bound_files = registry_source_files(registry)

        first = check_clone_remote(config)
        # Клон не адресован — в лечении красных стоит имя переменной, а не пустая
        # строка: `git -C "$ALTREGO_VAULT_CLONE" pull --ff-only` исполним как есть.
        clone_raw = vault_clone() or f'"${ENV_CLONE}"'
        second = check_vault_master(config, clone_raw, first["row"].get("head"),
                                    bound_files)
        for channel in (first, second):
            infra_rows.append(channel["row"])
            reds.extend(channel["reds"])
            warnings.extend(channel["warnings"])

        scenarios = load_scenarios(rel(paths.get("scenarios", "scenarios")))
        rc = check_registry(config, registry, scenarios)
        reg_rows = rc["rows"]
        reds.extend(rc["reds"])
        warnings.extend(rc["warnings"])
        mode = "full"

    exit_code = 1 if reds else 0
    payload = {
        "mode": mode,
        "kind": args.kind,
        "timestamp": ts,
        "files_checked": files_checked,
        "checkers": [c["name"] for c in checkers],
        "checkers_skipped": skipped,
        "ignores_applied": ignores_applied,
        "findings": [{"file": repo_rel(p), "line": f.line, "checker": f.checker,
                      "severity": f.severity, "message": f.message,
                      "observation": observe(observations, repo_rel(p), f.line,
                                             f.checker)}
                     for p, f in sorted(findings, key=lambda x: (x[0], x[1].line,
                                                                 x[1].checker))],
        "observations": observations,
        "not_measured": not_measured,
        "meta": meta_rows,
        "registry": reg_rows,
        "infra": infra_rows,
        "reds": reds,
        "warnings": warnings,
        "verdict": "КРАСНЫЙ" if reds else "ЗЕЛЁНЫЙ",
        "exit_code": exit_code,
    }

    print(f"# Прогон линтера — {mode} — вид {args.kind} — {ts} UTC")
    if skipped:
        print("Не запускались на этом виде: " + ", ".join(c["name"] for c in skipped))
    print()
    print("## Находки")
    if payload["findings"]:
        for f in payload["findings"]:
            mark = f" [наблюдение: {f['observation']}]" if f.get("observation") else ""
            print(f"{f['file']}:{f['line']} {f['checker']}{mark} {f['message']}")
    else:
        print("— пусто")
    print()
    print("## Сводка")
    print(f"- артефактов: {files_checked}; чекеров: {len(checkers)}; "
          f"находок: {len(payload['findings'])}")
    print(f"- изъятий применено: {ignores_applied}")
    for o in observations:
        print(f"- наблюдение [{o['class']}]: {o['file']}:{o['line']} {o['checker']} "
              f"— совпало находок: {o['matched']}")
    for r in not_measured:
        print(f"- не измерено: {r['file']} {r['checker']} — "
              f"{r.get('reason') or r.get('gate')}")
    for r in infra_rows:
        print(f"- инфраструктура [{r['class']}]: {r['check']} — {r['status']}"
              + (f"; {r['message']}" if r.get("message") else ""))
    if meta_rows:
        def cell(v):
            return "не-измерено" if v is None else v
        print("- калибровка: " + "; ".join(
            f"{r['checker']} red={cell(r['red_findings'])} "
            f"green={cell(r['green_findings'])} [{r['status']}]" for r in meta_rows))
    if reg_rows:
        print("- реестр ↔ вольт: " + "; ".join(
            f"{r['rule_id']} {str(r['recomputed'])[:8]} "
            f"[{r['status']}] ←{','.join(r['scenarios']) or '—'}" for r in reg_rows))
    print(f"- красных: {len(reds)}")
    for r in reds:
        print(f"  - {r}")
    print(f"- предупреждений: {len(warnings)}")
    for w in warnings:
        print(f"  - {w}")
    print(f"- вердикт: {payload['verdict']} (код выхода {exit_code})")

    if not args.no_report:
        md_path, json_path = write_report(config, payload)
        print(f"- отчёт: {repo_rel(md_path)}, {repo_rel(json_path)}")

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
