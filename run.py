#!/usr/bin/env python3
"""Прогон хода-линтера полосы «Система».

  run.py --fast <файлы…>   все чекеры по переданным артефактам
  run.py --full            то же по фикстурам + мета-тест + пересчёт хэшей реестра
  run.py … --kind spec     вид артефакта: handoff (умолчание) либо spec

Вид артефакта отбирает чекеры по полю kinds манифеста: чекер, которому в
задании нечего мерить, на нём и не говорит.

Находка изымается маркером `<!-- lint:ignore <checker> — причина -->` на
предыдущей строке; изъятие без причины само даёт находку (linter/ignores.py).

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

    Возвращает (активные, пропущенные). Чекер без поля kinds работает на всех
    видах: умолчание не должно молча выключать чекер.
    """
    shared = manifest.get("shared") or {}
    active, skipped = [], []
    for entry in manifest.get("checkers") or []:
        kinds = list(entry.get("kinds") or KINDS)
        if kind not in kinds:
            skipped.append({"name": entry["name"], "kinds": kinds})
            continue
        mod = importlib.import_module(entry["module"])
        cfg = dict(shared)
        cfg.update(entry.get("config") or {})
        active.append({"name": entry["name"], "module": mod, "config": cfg,
                       "kinds": kinds, "meta": entry})
    return active, skipped


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

def run_file(checkers, path: str) -> tuple[list[tuple[str, Finding]], int]:
    """Находки по одному артефакту после применения маркеров изъятия.

    Возвращает (находки, число применённых изъятий).
    """
    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()
    raw: list[Finding] = []
    for ch in checkers:
        raw.extend(ch["module"].check(text, ch["config"]))
    kept, applied = ignores.apply(text, raw)
    return [(path, f) for f in kept], applied


def repo_rel(path: str) -> str:
    """Путь относительно корня репозитория; вне репозитория — абсолютный."""
    try:
        r = os.path.relpath(os.path.abspath(path), ROOT)
    except ValueError:
        return os.path.abspath(path)
    return os.path.abspath(path) if r.startswith("..") else r


# ───────────────────── свежесть канона под прогоном ──────────────────────

DEFAULT_CLONE = "~/vaults/theygrow-vault"

# Пути канона внутри вольта: правка вне них хэшей реестра не касается.
DEFAULT_CANON_PATHS = ("00-system/", "01-theygrow/operations/", "02-synthesis/")

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


def check_clone_remote(config: dict) -> dict:
    """Канал 1: клон вольта против своей удалённой ветки.

    Хэш, посчитанный по отставшему клону, ничего не говорит о каноне: прогон
    получил бы зелёный на устаревшем предмете. Поэтому отставание — красный,
    но класса infra: это отказ опоры прогона, а не вердикт чекера об артефакте.
    Типизированные итоги: ok | clone_behind_vault | clone_freshness_unknown |
    clone_missing. Сам вызов читающий: fetch трогает только remote-ссылки клона,
    рабочее дерево вольта не меняется.
    """
    raw = (config.get("vault") or {}).get("clone_path", DEFAULT_CLONE)
    clone = os.path.expanduser(raw)
    row = {"class": INFRA, "check": "clone_freshness", "clone": raw,
           "branch": None, "upstream": None, "head": None, "remote_head": None,
           "behind": None, "ahead": None, "status": "ok", "message": None}
    reds: list[str] = []
    warns: list[str] = []

    def fail(status: str, message: str) -> dict:
        row["status"], row["message"] = status, message
        reds.append(f"[{INFRA}] {status}: {message}")
        return {"row": row, "reds": reds, "warnings": warns}

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


def check_vault_master(config: dict, clone_raw: str, clone_head: str | None) -> dict:
    """Канал 2: рабочая копия вольта против клона.

    Канала 1 мало: он сравнивает клон с его remote и зеленеет ровно тогда,
    когда правка канона лежит в рабочей копии вольта незакоммиченной — клон
    свеж относительно origin, а предмет, по которому считаются хэши, уже
    другой. Здесь читается сама рабочая копия: `git status --porcelain` и
    `git rev-parse HEAD`. Оба вызова читающие, в вольт ничего не пишется.

    Типизированные итоги: ok | vault_uncommitted | vault_ahead_of_clone |
    vault_master_unreachable. Недоступность пути (не WSL, диск не смонтирован,
    git отказал читать чужой по владельцу репозиторий) — предупреждение, а не
    зелёный: свежесть не измерена, а не подтверждена.
    """
    vault = config.get("vault") or {}
    raw = vault.get("master_path")
    canon_paths = tuple(vault.get("canon_paths") or DEFAULT_CANON_PATHS)
    master = os.path.expanduser(raw) if raw else None
    row = {"class": INFRA, "check": "vault_master", "master": raw,
           "canon_paths": list(canon_paths), "head": None, "clone_head": clone_head,
           "dirty_canon": None, "ahead": None, "status": "ok", "message": None}
    reds: list[str] = []
    warns: list[str] = []

    def unmeasured(message: str) -> dict:
        row["status"], row["message"] = "vault_master_unreachable", message
        warns.append(f"[{INFRA}] vault_master_unreachable: {message}")
        return {"row": row, "reds": reds, "warnings": warns}

    def dubious(action: str) -> dict:
        return unmeasured(
            f"git отказался читать {raw} на `{action}`: dubious ownership — "
            f"репозиторий принадлежит другому пользователю (штатный случай для "
            f"/mnt/… из WSL, не поломка вольта). Лечение владельцу: "
            f"`git config --global --add safe.directory {master}`; до него "
            f"незакоммиченная правка канона прогоном не измерена")

    if not raw:
        return unmeasured("в config.yaml нет vault.master_path — рабочая копия "
                          "вольта не читалась: незакоммиченная правка канона "
                          "прогоном не измерена")
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
    if dirty:
        shown = ", ".join(dirty[:4])
        tail = f" и ещё {len(dirty) - 4}" if len(dirty) > 4 else ""
        statuses.append("vault_uncommitted")
        reds.append(f"[{INFRA}] vault_uncommitted: в рабочей копии вольта {raw} "
                    f"незакоммичены изменения по путям канона ({shown}{tail}) — "
                    f"клон их не видит, хэши считались бы по канону без этих правок; "
                    f"владельцу закоммитить и запушить их, затем "
                    f"`git -C {clone_raw} pull --ff-only` и повторить прогон")

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
        if ahead == 0:
            warns.append(f"[{INFRA}] vault_behind_clone: HEAD рабочей копии вольта "
                         f"{raw} ({head[:8]}) — предок HEAD клона ({clone_head[:8]}): "
                         f"клон читает канон новее, чем лежит в вольте")
        else:
            statuses.append("vault_ahead_of_clone")
            detail = (f"на {ahead} коммит(ов)" if ahead
                      else f"коммит {head[:8]} клону неизвестен (не запушен)")
            reds.append(f"[{INFRA}] vault_ahead_of_clone: HEAD рабочей копии вольта "
                        f"{raw} ({head[:8]}) впереди клона ({clone_head[:8]}) {detail} — "
                        f"клон читает устаревший канон; владельцу запушить вольт, затем "
                        f"`git -C {clone_raw} pull --ff-only` и повторить прогон")

    row["status"] = "+".join(statuses) if statuses else "ok"
    row["message"] = (f"HEAD {head[:8]}"
                      + (f" ↔ клон {clone_head[:8]}" if clone_head else "")
                      + f", незакоммиченных путей канона: {len(dirty)}")
    return {"row": row, "reds": reds, "warnings": warns}


def check_clone_freshness(config: dict) -> dict:
    """Свежесть канона под прогоном — двумя независимыми каналами.

    Канал 1 (клон ↔ его remote) и канал 2 (рабочая копия вольта ↔ клон).
    Одного первого мало: он зеленеет, пока правка канона не закоммичена.
    """
    first = check_clone_remote(config)
    clone_raw = (config.get("vault") or {}).get("clone_path", DEFAULT_CLONE)
    second = check_vault_master(config, clone_raw, first["row"].get("head"))
    return {"rows": [first["row"], second["row"]],
            "reds": first["reds"] + second["reds"],
            "warnings": first["warnings"] + second["warnings"]}


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
    clone = (config.get("vault") or {}).get("clone_path", DEFAULT_CLONE)
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


# ──────────────────────────────── мета-тест ──────────────────────────────

def meta_test(checkers, fixtures_dir: str):
    reds: list[str] = []
    warns: list[str] = []
    rows: list[dict] = []
    findings: list[tuple[str, Finding]] = []

    per_file: dict[str, list[Finding]] = {}
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
            got, applied = run_file(checkers, path)
            applied_total += applied
            findings.extend(got)
            per_file[path] = [f for _, f in got]

    for ch in checkers:
        name = ch["name"]
        red_path = os.path.join(fixtures_dir, "red", f"{name}.md")
        green_path = os.path.join(fixtures_dir, "green", f"{name}.md")
        row = {"checker": name, "red_findings": None, "green_findings": None,
               "status": "ok"}

        if not os.path.isfile(red_path):
            reds.append(f"{name}: нет red-фикстуры {repo_rel(red_path)}")
            row["status"] = "no-red-fixture"
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
        else:
            own = [f for f in per_file.get(green_path, []) if f.checker == name]
            row["green_findings"] = len(own)
            if own:
                reds.append(f"{name}: покраснел на своей green-фикстуре "
                            f"{repo_rel(green_path)} ({len(own)} наход.) — ложное срабатывание")
                row["status"] = "green-fixture-red"
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

    return {"rows": rows, "reds": reds, "warnings": warns, "findings": findings,
            "ignores_applied": applied_total}


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
        L.append("## Мета-тест чекеров")
        L.append("")
        L.append("| чекер | red-фикстура | green-фикстура | итог |")
        L.append("|---|---|---|---|")
        for r in payload["meta"]:
            L.append(f"| {r['checker']} | {r['red_findings']} | "
                     f"{r['green_findings']} | {r['status']} |")
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
    checkers, skipped = load_checkers(manifest, args.kind)
    observations = load_observations(config)

    ts = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    findings: list[tuple[str, Finding]] = []
    reds: list[str] = []
    warnings: list[str] = []
    meta_rows = None
    reg_rows = None
    infra_rows: list[dict] = []
    ignores_applied = 0
    files_checked = 0

    if args.fast is not None:
        for path in targets:
            if not os.path.isfile(path):
                reds.append(f"нет файла: {path}")
                continue
            files_checked += 1
            got, applied = run_file(checkers, path)
            ignores_applied += applied
            findings.extend(got)
        # В сводке — только адрес находки: сообщение уже стоит в списке выше.
        for path, f in findings:
            addr = f"{repo_rel(path)}:{f.line} {f.checker}"
            (reds if f.severity in FAILING else warnings).append(addr)
        mode = "fast"
    else:
        fixtures = rel(paths.get("fixtures", "linter/fixtures"))
        mt = meta_test(checkers, fixtures)
        findings = mt["findings"]
        files_checked = sum(
            1 for c in ("red", "green")
            if os.path.isdir(os.path.join(fixtures, c))
            for f in os.listdir(os.path.join(fixtures, c)) if f.endswith(".md"))
        meta_rows = mt["rows"]
        ignores_applied += mt["ignores_applied"]
        reds.extend(mt["reds"])
        warnings.extend(mt["warnings"])

        # Свежесть клона — предпосылка пересчёта хэшей: меряется до него.
        fresh = check_clone_freshness(config)
        infra_rows.extend(fresh["rows"])
        reds.extend(fresh["reds"])
        warnings.extend(fresh["warnings"])

        registry = load_yaml(rel(paths.get("registry", "rules/registry.yaml"))) or {}
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
    for r in infra_rows:
        print(f"- инфраструктура [{r['class']}]: {r['check']} — {r['status']}"
              + (f"; {r['message']}" if r.get("message") else ""))
    if meta_rows:
        print("- мета-тест: " + "; ".join(
            f"{r['checker']} red={r['red_findings']} green={r['green_findings']} "
            f"[{r['status']}]" for r in meta_rows))
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
