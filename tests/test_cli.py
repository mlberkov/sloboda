"""Контракт CLI `run.py`: коды выхода, `--kind`, `--no-report`, вольт.

Каждое утверждение исполняется вызовом `run.main(...)`, а не чтением кода:
«линтер краснеет» и «линтер не ходит в вольт» — утверждения о рантайме.
"""

from __future__ import annotations

import glob
import os

import pytest

import run
from linter import canon

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GREEN = sorted(glob.glob(os.path.join(ROOT, "linter", "fixtures", "green", "*.md")))
RED_ONE = os.path.join(ROOT, "linter", "fixtures", "red", "whitespace_diff.md")


def test_green_fixture_corpus_is_not_empty():
    assert len(GREEN) == 16


def test_fast_green_exit_zero(capsys):
    assert run.main(["--fast", *GREEN, "--no-report"]) == 0
    out = capsys.readouterr().out
    assert "- красных: 0" in out
    assert "артефактов: 16; чекеров: 16; находок: 0" in out


def test_fast_red_exit_one(capsys):
    assert run.main(["--fast", RED_ONE, "--no-report"]) == 1
    assert "- красных: 1" in capsys.readouterr().out


def test_positional_files_equal_fast_list(capsys):
    assert run.main(["--fast", "--no-report", GREEN[0]]) == 0
    assert "артефактов: 1" in capsys.readouterr().out


def test_missing_file_is_red(capsys):
    assert run.main(["--fast", os.path.join(ROOT, "нет-такого.md"), "--no-report"]) == 1
    assert "нет файла" in capsys.readouterr().out


def test_kind_spec_skips_handoff_only_checkers(capsys):
    assert run.main(["--fast", GREEN[0], "--kind", "spec", "--no-report"]) == 0
    out = capsys.readouterr().out
    skipped = out.splitlines()[1]
    assert skipped.startswith("Не запускались на этом виде: ")
    names = skipped.split(": ", 1)[1].split(", ")
    assert len(names) == 10 and "turn_end" in names and "sources_trailer" in names
    assert "чекеров: 6;" in out


def test_kind_default_is_handoff(capsys):
    run.main(["--fast", GREEN[0], "--no-report"])
    assert "вид handoff" in capsys.readouterr().out


def test_no_report_writes_nothing(monkeypatch, tmp_path, vault_pair):
    config = vault_pair.config()
    monkeypatch.setattr(run, "load_config", lambda: config)
    run.main(["--fast", GREEN[0], "--no-report"])
    assert os.listdir(vault_pair.reports) == []


def test_report_pair_is_written(monkeypatch, capsys, vault_pair):
    config = vault_pair.config()
    monkeypatch.setattr(run, "load_config", lambda: config)
    run.main(["--fast", GREEN[0]])
    written = sorted(os.listdir(vault_pair.reports))
    assert len(written) == 2
    assert written[0].endswith(".json") and written[1].endswith(".md")
    assert "- отчёт: " in capsys.readouterr().out


@pytest.mark.parametrize("argv", [[], ["--fast"], ["--full", "a.md"],
                                  ["--fast", "a.md", "--kind", "нет-такого"]])
def test_argparse_contract(argv):
    with pytest.raises(SystemExit) as exc:
        run.main(argv)
    assert exc.value.code == 2


def test_fast_touches_no_vault(monkeypatch):
    """`--fast` вольта не касается: ни git-вызовов, ни пересчёта хэшей.

    Утверждение о рантайме, поэтому и меряется рантаймом: обе двери в вольт
    закрываются взрывающимися заглушками, и прогон обязан пройти мимо них.
    """
    def boom(*args, **kwargs):
        raise AssertionError("--fast полез в вольт")

    monkeypatch.setattr(run, "_git", boom)
    monkeypatch.setattr(canon, "hash_rule", boom)
    assert run.main(["--fast", *GREEN, "--no-report"]) == 0
