"""Fail-closed механики прогона: отказ контролёра — отказ хода, не разрешение.

Опора: несущий документ, §3, слой 0, пункт 5 (owner-акт 2026-09-04); ADR-054,
аннотация (1) и (3); контракт §11, «Исполнимость shell-блока» (3) — отличимость
провала. Форма взята у vale: ошибка правила есть ошибка прогона с именем правила,
лимит — та же ошибка, а не отсутствие решения.

Каждое утверждение о рантайме исполняется вызовом `run.main(...)`, а не чтением
кода: «прогон краснеет», «находки дисквалифицированного не входят в вердикт» —
утверждения о рантайме. Статически меряются только статические свойства файлов
(отсутствие глотающих обработчиков в чекерах) — тем же приёмом, что контракт
имени job'а в tests/test_ci_contract.py.

Единица диспозиции — «на прогон».
"""

from __future__ import annotations

import ast
import glob
import os
import shutil
import sys
import time

import pytest

import run
from linter import common
from linter.checkers import artifact_integrity, download_dir, whitespace_diff
from tests.conftest import infra_lines, parse_run_output

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(ROOT, "linter", "fixtures")
GREEN = sorted(glob.glob(os.path.join(FIXTURES, "green", "*.md")))

# Маркер, по которому подменённый чекер отказывает. Без него чекер упал бы и на
# собственной фикстуре, был бы дисквалифицирован калибровкой и до цели не дошёл
# бы вовсе — путь `checker_error` на артефакте остался бы не измерен.
TARGET = "МАРКЕР-ОТКАЗА"

# Настоящие функции чекеров, снятые до подмены: подменённая обёртка зовёт их на
# фикстурах калибровки. Через модуль их звать нельзя — там уже стоит обёртка,
# и вызов ушёл бы в бесконечную рекурсию.
REAL_WHITESPACE = whitespace_diff.check
REAL_INTEGRITY = artifact_integrity.check


# ────────────────────────────── инструменты ──────────────────────────────

def config_with(tmp_path, **over) -> dict:
    """Конфиг прогона: настоящие манифест и фикстуры, отчёты — во временный."""
    cfg = {
        "paths": {"registry": os.path.join(ROOT, "rules", "registry.yaml"),
                  "scenarios": os.path.join(ROOT, "scenarios"),
                  "manifest": "linter/manifest.yaml",
                  "fixtures": FIXTURES,
                  "reports": str(tmp_path / "reports")},
        "canon": {"section_parser": "bold_lead_v1"},
        "limits": {"checker_timeout_seconds": 2},
        "observations": [],
    }
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(cfg.get(key), dict):
            cfg[key] = {**cfg[key], **value}
        else:
            cfg[key] = value
    return cfg


def target_file(tmp_path, source: str, name: str = "цель.md") -> str:
    """Копия фикстуры с маркером отказа: цель прогона, не фикстура полосы."""
    with open(source, encoding="utf-8") as fh:
        text = fh.read()
    path = tmp_path / name
    path.write_text(f"{text}\n\n<!-- {TARGET} -->\n", encoding="utf-8")
    return str(path)


def fixtures_copy(tmp_path) -> str:
    """Копия каталога фикстур: мутации ставятся на ней, предмет не правится."""
    dst = str(tmp_path / "fixtures")
    shutil.copytree(FIXTURES, dst)
    return dst


def run_fast(monkeypatch, capsys, config: dict, *targets: str):
    monkeypatch.setattr(run, "load_config", lambda: config)
    code = run.main(["--fast", *targets, "--no-report"])
    out = capsys.readouterr().out
    return code, parse_run_output(out), out


def not_measured_lines(out: str) -> list[str]:
    return [ln for ln in out.splitlines() if ln.startswith("- не измерено: ")]


# ──────────────────── отказ чекера на целевом артефакте ──────────────────

def test_checker_exception_is_red_naming_checker_and_frame(monkeypatch, capsys,
                                                           tmp_path):
    """Исключение внутри чекера: красный с именем чекера, типом и кадром.

    До этой правки исключение вылетало из `main` целиком: traceback без строки
    вердикта, без отчёта и без имени чекера. Отказ контролёра читался как
    поломка прогона, а не как его вердикт.
    """
    def boom(text, config):
        if TARGET in text:
            return common.parse_blocks(None)      # кадр ляжет в linter/common.py
        return REAL_WHITESPACE(text, config)

    monkeypatch.setattr(whitespace_diff, "check", boom)
    target = target_file(tmp_path, os.path.join(FIXTURES, "red", "download_dir.md"))
    code, seen, out = run_fast(monkeypatch, capsys, config_with(tmp_path), target)

    assert code == 1
    (red,) = infra_lines(seen["reds"], run.CHECKER_ERROR)
    assert "whitespace_diff" in red
    assert "AttributeError" in red
    assert "linter/common.py:" in red and "in split_lines" in red
    assert os.path.basename(target) in red
    # Отказ одного чекера не снимает вердикт с остальных.
    assert any("download_dir" in ln for ln in out.splitlines()
               if ln.startswith(os.path.basename(target)) or "цель.md:" in ln)
    assert any("whitespace_diff — отказ чекера: AttributeError" in ln
               for ln in not_measured_lines(out))


def test_checker_system_exit_does_not_end_run_green(monkeypatch, capsys, tmp_path):
    """`sys.exit()` внутри чекера: красный, а не тихий нулевой код выхода.

    `except Exception` его не ловит — прогон завершился бы кодом 0 с пустой
    выдачей, то есть отказ контролёра стал бы разрешением.
    """
    def bail(text, config):
        if TARGET in text:
            sys.exit(0)
        return REAL_WHITESPACE(text, config)

    monkeypatch.setattr(whitespace_diff, "check", bail)
    target = target_file(tmp_path, os.path.join(FIXTURES, "green", "turn_end.md"))
    code, seen, _out = run_fast(monkeypatch, capsys, config_with(tmp_path), target)

    assert code == 1
    (red,) = infra_lines(seen["reds"], run.CHECKER_ERROR)
    assert "whitespace_diff" in red and "SystemExit" in red


def test_gate_failure_silences_what_it_guards(monkeypatch, capsys, tmp_path):
    """Отказ гейта гасит охраняемых: не измерен вход — не измерено и охраняемое."""
    def boom(text, config):
        if TARGET in text:
            raise ValueError("гейт отказал")
        return REAL_INTEGRITY(text, config)

    monkeypatch.setattr(artifact_integrity, "check", boom)
    target = target_file(tmp_path, os.path.join(FIXTURES, "green", "shell_mech.md"))
    code, seen, out = run_fast(monkeypatch, capsys, config_with(tmp_path), target)

    assert code == 1
    (red,) = infra_lines(seen["reds"], run.CHECKER_ERROR)
    assert "artifact_integrity" in red and "ValueError" in red
    guarded = artifact_integrity.gated({"gates": None})
    silenced = " ".join(not_measured_lines(out))
    for name in ("shell_mech", "smoke_line", "grep_vacuum"):
        assert name in guarded or name in silenced
        assert f"{name} — гейт artifact_integrity не измерен" in silenced


# ─────────────────────────────── лимит времени ───────────────────────────

def test_checker_over_limit_is_red_with_measured_time(monkeypatch, capsys, tmp_path):
    """Превышение лимита: красный с именем, файлом, измеренным временем и лимитом."""
    def slow(text, config):
        if TARGET in text:
            stop = time.process_time() + 0.05
            while time.process_time() < stop:
                pass
            return []
        return REAL_WHITESPACE(text, config)

    monkeypatch.setattr(whitespace_diff, "check", slow)
    target = target_file(tmp_path, os.path.join(FIXTURES, "green", "turn_end.md"))
    config = config_with(tmp_path, limits={"checker_timeout_seconds": 0.01})
    code, seen, out = run_fast(monkeypatch, capsys, config, target)

    assert code == 1
    (red,) = infra_lines(seen["reds"], run.CHECKER_TIMEOUT)
    assert "whitespace_diff" in red and "при лимите 0.01s" in red
    assert "s при лимите" in red
    assert any("whitespace_diff — лимит 0.01s" in ln for ln in not_measured_lines(out))


def test_limit_does_not_fire_on_the_real_corpus(monkeypatch, capsys, tmp_path):
    """Штатный лимит на настоящем корпусе не срабатывает: гард не шумит."""
    code, seen, _out = run_fast(monkeypatch, capsys, config_with(tmp_path), *GREEN)
    assert code == 0
    assert infra_lines(seen["reds"], run.CHECKER_TIMEOUT) == []


@pytest.mark.parametrize("limits", [{"checker_timeout_seconds": 0}, {}, None,
                                    {"checker_timeout_seconds": "две секунды"}])
def test_absent_or_zero_limit_is_red(monkeypatch, capsys, tmp_path, limits):
    """Лимит не задан либо снят нулём — отказ конфигурации, а не «без лимита»."""
    config = config_with(tmp_path)
    if limits is None:
        config.pop("limits")
    else:
        config["limits"] = limits
    code, seen, _out = run_fast(monkeypatch, capsys, config, GREEN[0])
    assert code == 1
    (red,) = infra_lines(seen["reds"], run.CONFIG_INVALID_LIMIT)
    assert run.LIMIT_KEY in red


# ───────────────────── калибровка на каждом прогоне ──────────────────────

def test_calibration_runs_in_fast(monkeypatch, capsys, tmp_path):
    """`--fast` несёт калибровку: 16 чекеров, все `ok`, находок ноль."""
    code, _seen, out = run_fast(monkeypatch, capsys, config_with(tmp_path), *GREEN)
    line = next(ln for ln in out.splitlines() if ln.startswith("- калибровка: "))
    assert code == 0
    assert line.count("[ok]") == 16
    assert "артефактов: 16; чекеров: 16; находок: 0" in out


def test_silent_red_fixture_disqualifies_checker(monkeypatch, capsys, tmp_path):
    """Провал калибровки: чекер в «Не измерено», его находки в вердикт не входят.

    До этой правки такой чекер продолжал голосовать нулём находок в `--fast`
    (калибровки там не было вовсе) — детектор, не умеющий краснеть, выглядел
    как детектор, которому нечего сказать.
    """
    fixtures = fixtures_copy(tmp_path)
    # Мутация ставится на копии: red-фикстура полосы — предмет измерения.
    with open(os.path.join(fixtures, "green", "shell_mech.md"), encoding="utf-8") as fh:
        green_text = fh.read()
    with open(os.path.join(fixtures, "red", "shell_mech.md"), "w",
              encoding="utf-8") as fh:
        fh.write(green_text)

    target = os.path.join(FIXTURES, "red", "shell_mech.md")
    config = config_with(tmp_path, paths={"fixtures": fixtures})
    code, seen, out = run_fast(monkeypatch, capsys, config, target)

    assert code == 1
    assert any("shell_mech: не покраснел на своей red-фикстуре" in r
               for r in seen["reds"])
    assert any("shell_mech — калибровка: red-fixture-silent" in ln
               for ln in not_measured_lines(out))
    # Находки дисквалифицированного не входят ни в раздел «Находки», ни в
    # вердикт: цель здесь — его собственная red-фикстура, на которой он даёт
    # 8 находок, пока измеряем. Дисквалифицированный не даёт ни одной.
    listed = out.split("## Находки", 1)[1].split("## Сводка", 1)[0]
    assert " shell_mech " not in listed
    assert not any(r.endswith(" shell_mech") for r in seen["reds"])


def test_green_fixture_red_disqualifies_checker(monkeypatch, capsys, tmp_path):
    """Ложное срабатывание на своей green-фикстуре — та же дисквалификация."""
    fixtures = fixtures_copy(tmp_path)
    with open(os.path.join(fixtures, "red", "turn_end.md"), encoding="utf-8") as fh:
        red_text = fh.read()
    with open(os.path.join(fixtures, "green", "turn_end.md"), "w",
              encoding="utf-8") as fh:
        fh.write(red_text)

    config = config_with(tmp_path, paths={"fixtures": fixtures})
    code, seen, out = run_fast(monkeypatch, capsys, config, GREEN[0])
    assert code == 1
    assert any("turn_end: покраснел на своей green-фикстуре" in r
               for r in seen["reds"])
    assert any("turn_end — калибровка: green-fixture-red" in ln
               for ln in not_measured_lines(out))


def test_checker_failing_on_own_fixture_is_disqualified(monkeypatch, capsys,
                                                        tmp_path):
    """Отказ на собственной фикстуре: «не измерен», а не «оправдан»."""
    monkeypatch.setattr(download_dir, "check",
                        lambda text, config: (_ for _ in ()).throw(
                            RuntimeError("отказ на калибровке")))
    code, seen, out = run_fast(monkeypatch, capsys, config_with(tmp_path), GREEN[0])

    assert code == 1
    reds = infra_lines(seen["reds"], run.CHECKER_ERROR)
    assert reds and all("download_dir" in r for r in reds)
    assert any("download_dir — калибровка: checker-error" in ln
               for ln in not_measured_lines(out))


# ───────────────── прочие отказы опоры прогона ───────────────────────────

def test_unreadable_artifact_is_red_not_traceback(monkeypatch, capsys, tmp_path):
    """Нечитаемый артефакт: типизированный красный с именем файла."""
    broken = tmp_path / "битый.md"
    broken.write_bytes(b"# \xff\xfe\x00 not utf-8")
    code, seen, _out = run_fast(monkeypatch, capsys, config_with(tmp_path),
                                str(broken))
    assert code == 1
    (red,) = infra_lines(seen["reds"], run.ARTIFACT_UNREADABLE)
    assert "битый.md" in red and "DecodeError" in red


def test_no_checkers_for_kind_is_red(monkeypatch, capsys, tmp_path):
    """Ноль чекеров на виде — не ноль находок, а неизмеренный вход."""
    manifest = tmp_path / "манифест.yaml"
    manifest.write_text("version: 0\nshared: {}\ncheckers: []\n", encoding="utf-8")
    config = config_with(tmp_path, paths={"manifest": str(manifest)})
    code, seen, _out = run_fast(monkeypatch, capsys, config, GREEN[0])
    assert code == 1
    (red,) = infra_lines(seen["reds"], run.NO_CHECKERS_FOR_KIND)
    assert "handoff" in red


def test_import_error_of_checker_is_red(monkeypatch, capsys, tmp_path):
    """Модуль чекера не импортируется — отказ опоры, а не чекер без находок."""
    manifest = tmp_path / "манифест.yaml"
    manifest.write_text(
        "version: 0\nshared: {}\ncheckers:\n"
        "  - name: нет_такого\n    module: linter.checkers.нет_такого\n",
        encoding="utf-8")
    config = config_with(tmp_path, paths={"manifest": str(manifest)})
    code, seen, _out = run_fast(monkeypatch, capsys, config, GREEN[0])
    assert code == 1
    (red,) = infra_lines(seen["reds"], run.CHECKER_IMPORT_ERROR)
    assert "нет_такого" in red


# ──────────────────────── статический гард по чекерам ────────────────────

def test_checkers_do_not_swallow_exceptions():
    """Чекер не глотает собственный отказ.

    `except Exception` (или голый `except:`) без `raise` в теле превратил бы
    отказ чекера в ноль находок мимо `checker_error` — ровно та пустая выдача
    при нулевом коде выхода, которую полоса и ловит. Свойство статическое,
    поэтому меряется разбором исходника: `ast` отличает `except ValueError`
    (законное сужение) от глотающего обработчика, а grep — нет.
    """
    swallowing: list[str] = []
    for path in sorted(glob.glob(os.path.join(ROOT, "linter", "**", "*.py"),
                                 recursive=True)):
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename=path)
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler):
                continue
            names = ({node.type.id} if isinstance(node.type, ast.Name)
                     else {e.id for e in getattr(node.type, "elts", [])
                           if isinstance(e, ast.Name)} if node.type else set())
            broad = node.type is None or bool(names & {"Exception", "BaseException"})
            if broad and not any(isinstance(n, ast.Raise) for n in ast.walk(node)):
                swallowing.append(f"{os.path.relpath(path, ROOT)}:{node.lineno}")
    assert swallowing == [], swallowing
