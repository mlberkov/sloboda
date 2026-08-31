#!/usr/bin/env python3
"""Прогон хода-линтера полосы «Система».

  run.py --fast <файлы…>   все чекеры по переданным артефактам
  run.py --full            то же по фикстурам + мета-тест + пересчёт хэшей реестра

Выход ≠ 0 при любом красном. Отчёт — reports/run-<UTC-timestamp>.md и .json.
"""

from __future__ import annotations

import argparse
import datetime as dt
import importlib
import json
import os
import sys

import yaml

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from linter import canon                                    # noqa: E402
from linter.common import RED, WARNING, Finding             # noqa: E402


# ─────────────────────────────── загрузка ────────────────────────────────

def load_yaml(path: str):
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def rel(path: str) -> str:
    return os.path.join(ROOT, path)


def load_config() -> dict:
    return load_yaml(rel("config.yaml")) or {}


def load_checkers(manifest: dict):
    shared = manifest.get("shared") or {}
    out = []
    for entry in manifest.get("checkers") or []:
        mod = importlib.import_module(entry["module"])
        cfg = dict(shared)
        cfg.update(entry.get("config") or {})
        out.append({"name": entry["name"], "module": mod, "config": cfg, "meta": entry})
    return out


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

def run_file(checkers, path: str) -> list[tuple[str, Finding]]:
    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()
    out = []
    for ch in checkers:
        for f in ch["module"].check(text, ch["config"]):
            out.append((path, f))
    return out


def repo_rel(path: str) -> str:
    """Путь относительно корня репозитория; вне репозитория — абсолютный."""
    try:
        r = os.path.relpath(os.path.abspath(path), ROOT)
    except ValueError:
        return os.path.abspath(path)
    return os.path.abspath(path) if r.startswith("..") else r


# ─────────────────────────── реестр и сценарии ───────────────────────────

def check_registry(config: dict, registry: dict, scenarios: list[dict]) -> dict:
    clone = (config.get("vault") or {}).get("clone_path", "~/vaults/theygrow-vault")
    reds: list[str] = []
    warns: list[str] = []
    rows: list[dict] = []

    by_rule: dict[str, list[dict]] = {}
    for s in scenarios:
        if s.get("status") == "active":
            by_rule.setdefault(s.get("rule_id"), []).append(s)

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
            "scenarios": [s["id"] for s in by_rule.get(rid, [])],
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

        for s in by_rule.get(rid, []):
            bound = s.get("rule_hash_at_binding")
            if recomputed == canon.UNRESOLVED:
                continue
            if bound != recomputed:
                row["status"] = "scenario-drift"
                reds.append(f"{s['id']} ↔ {rid}: rule_hash_at_binding "
                            f"{str(bound)[:8]} ≠ вольт {recomputed[:8]} — "
                            f"пункт канона изменился после связывания сценария")

        if not by_rule.get(rid):
            warns.append(f"{rid}: нет активного сценария — мёртвая норма")
        rows.append(row)

    known = {r["rule_id"] for r in (registry.get("rules") or [])}
    for s in scenarios:
        if s.get("rule_id") not in known:
            reds.append(f"{s.get('id')}: rule_id {s.get('rule_id')} отсутствует в реестре")

    return {"rows": rows, "reds": reds, "warnings": warns}


# ──────────────────────────────── мета-тест ──────────────────────────────

def meta_test(checkers, fixtures_dir: str):
    reds: list[str] = []
    warns: list[str] = []
    rows: list[dict] = []
    findings: list[tuple[str, Finding]] = []

    per_file: dict[str, list[Finding]] = {}
    for colour in ("red", "green"):
        d = os.path.join(fixtures_dir, colour)
        if not os.path.isdir(d):
            reds.append(f"нет каталога фикстур {repo_rel(d)}")
            continue
        for fn in sorted(os.listdir(d)):
            if not fn.endswith(".md"):
                continue
            path = os.path.join(d, fn)
            got = run_file(checkers, path)
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

    green_dir = os.path.join(fixtures_dir, "green")
    for path, fs in per_file.items():
        if os.path.dirname(path) != green_dir:
            continue
        own = os.path.splitext(os.path.basename(path))[0]
        for f in fs:
            if f.checker != own:
                warns.append(f"{repo_rel(path)}:{f.line} чужой чекер {f.checker} "
                             f"покраснел на green-фикстуре — {f.message}")

    return {"rows": rows, "reds": reds, "warnings": warns, "findings": findings}


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

    L = [f"# Прогон линтера — {payload['mode']} — {ts} UTC", ""]
    L.append(f"Артефактов проверено: {payload['files_checked']}. "
             f"Чекеров: {', '.join(payload['checkers'])}.")
    L.append("")

    L.append("## Находки")
    L.append("")
    if payload["findings"]:
        for f in payload["findings"]:
            L.append(f"{f['file']}:{f['line']} {f['checker']} {f['message']}")
    else:
        L.append("— пусто")
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
        L.append("| rule_id | хэш реестра | хэш вольта | строки | сценарии | итог |")
        L.append("|---|---|---|---|---|---|")
        for r in payload["registry"]:
            L.append(f"| {r['rule_id']} | `{str(r['stored'])[:8]}` | "
                     f"`{str(r['recomputed'])[:8]}` | {r['lines'] or '—'} | "
                     f"{', '.join(r['scenarios']) or '—'} | {r['status']} |")
        L.append("")

    L.append("## Сводка")
    L.append("")
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
    g.add_argument("--fast", nargs="+", metavar="ФАЙЛ",
                   help="все чекеры по переданным артефактам")
    g.add_argument("--full", action="store_true",
                   help="фикстуры + мета-тест + пересчёт хэшей реестра")
    ap.add_argument("--no-report", action="store_true",
                    help="не писать файл отчёта (отчёт только в stdout)")
    args = ap.parse_args(argv)

    config = load_config()
    paths = config.get("paths") or {}
    manifest = load_yaml(rel(paths.get("manifest", "linter/manifest.yaml"))) or {}
    checkers = load_checkers(manifest)

    ts = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    findings: list[tuple[str, Finding]] = []
    reds: list[str] = []
    warnings: list[str] = []
    meta_rows = None
    reg_rows = None
    files_checked = 0

    if args.fast:
        for path in args.fast:
            if not os.path.isfile(path):
                reds.append(f"нет файла: {path}")
                continue
            files_checked += 1
            findings.extend(run_file(checkers, path))
        # В сводке — только адрес находки: сообщение уже стоит в списке выше.
        for path, f in findings:
            addr = f"{repo_rel(path)}:{f.line} {f.checker}"
            (reds if f.severity == RED else warnings).append(addr)
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
        reds.extend(mt["reds"])
        warnings.extend(mt["warnings"])

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
        "timestamp": ts,
        "files_checked": files_checked,
        "checkers": [c["name"] for c in checkers],
        "findings": [{"file": repo_rel(p), "line": f.line, "checker": f.checker,
                      "severity": f.severity, "message": f.message}
                     for p, f in sorted(findings, key=lambda x: (x[0], x[1].line,
                                                                 x[1].checker))],
        "meta": meta_rows,
        "registry": reg_rows,
        "reds": reds,
        "warnings": warnings,
        "verdict": "КРАСНЫЙ" if reds else "ЗЕЛЁНЫЙ",
        "exit_code": exit_code,
    }

    print(f"# Прогон линтера — {mode} — {ts} UTC")
    print()
    print("## Находки")
    if payload["findings"]:
        for f in payload["findings"]:
            print(f"{f['file']}:{f['line']} {f['checker']} {f['message']}")
    else:
        print("— пусто")
    print()
    print("## Сводка")
    print(f"- артефактов: {files_checked}; чекеров: {len(checkers)}; "
          f"находок: {len(payload['findings'])}")
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
