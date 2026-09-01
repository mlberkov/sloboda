"""Сужение infra-детекторов канала 2 по связанным файлам реестра.

Повод (реестр §D, 2026-08-31, «триггер по форме против триггера по инварианту»):
незакоммиченная запись оркестратора в `00-system/log.md` красила прогон, хотя ни
один `content_hash` от неё не зависит. Красным остаётся расхождение по файлам,
названным в `rules[].source.file`; набор выводится из реестра в момент прогона.

Диспозиция меряется на синтетической паре вольта (`tests/conftest.py`), единица —
«на прогон `--full`». Вольт владельца тесты не трогают.
"""

from __future__ import annotations

import os

import pytest
import yaml

import run
from tests.conftest import CONTRACT, LOG, PROMPT_KIT, infra_lines

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ─────────────────────── набор связанных файлов ──────────────────────────

def test_bound_files_from_real_registry():
    """Набор выводится из реестра, а не из списка в конфиге."""
    with open(os.path.join(ROOT, "rules", "registry.yaml"), encoding="utf-8") as fh:
        registry = yaml.safe_load(fh)
    assert run.registry_source_files(registry) == {
        "01-theygrow/operations/theygrow-delivery-contract.md",
        "01-theygrow/operations/theygrow-delivery-prompt-kit.md",
    }


def test_bound_files_empty_registry():
    assert run.registry_source_files({}) == set()
    assert run.registry_source_files({"rules": [{"source": {"file": "  "}}]}) == set()


def test_is_bound_direct_and_untracked_dir():
    bound = {"01-theygrow/operations/contract.md"}
    assert run.is_bound("01-theygrow/operations/contract.md", bound)
    assert not run.is_bound("00-system/log.md", bound)
    # git показывает неотслеживаемый каталог одной строкой: правка связанного
    # файла внутри него видна только префиксом.
    assert run.is_bound("01-theygrow/operations/", bound)
    assert not run.is_bound("00-system/", bound)


def test_split_bound_without_registry_keeps_old_behaviour():
    """Сужать нечем — все пути считаются связанными, как до сужения."""
    assert run.split_bound(["a", "b"], set()) == (["a", "b"], [])


def test_evidence_names_both_sets():
    text = run.evidence(["c.md"], ["log.md"], {"c.md"})
    assert "связанные с правилами реестра: c.md" in text
    assert "прочие пути канона: log.md" in text


# ───────────────────────── мутации, на прогон --full ─────────────────────

def channel_two(bucket: list[str], status: str) -> list[str]:
    return infra_lines(bucket, status)


def test_mutation_0_baseline(vault_pair, full_run):
    code, seen, _ = full_run(vault_pair.config())
    assert (code, seen["reds"], seen["warnings"]) == (0, [], [])


def test_mutation_a_unbound_file(vault_pair, full_run):
    """(а) незакоммиченная правка 00-system/log.md."""
    vault_pair.touch(LOG)
    code, seen, _ = full_run(vault_pair.config())
    assert code == 0
    assert seen["reds"] == []
    assert len(seen["warnings"]) == 1
    assert len(channel_two(seen["warnings"], "vault_uncommitted_unbound")) == 1
    assert LOG in seen["warnings"][0]


def test_mutation_b_bound_file(vault_pair, full_run):
    """(б) незакоммиченная правка файла, названного в rules[].source.file."""
    vault_pair.touch(CONTRACT)
    code, seen, _ = full_run(vault_pair.config())
    assert code == 1
    assert len(seen["reds"]) == 1
    assert len(channel_two(seen["reds"], "vault_uncommitted")) == 1
    assert seen["warnings"] == []


def test_mutation_c_both_files(vault_pair, full_run):
    """(в) правки обоих файлов: вердикт держит связанный набор."""
    vault_pair.touch(LOG)
    vault_pair.touch(CONTRACT)
    code, seen, _ = full_run(vault_pair.config())
    assert code == 1
    assert len(seen["reds"]) == 1
    assert len(channel_two(seen["reds"], "vault_uncommitted")) == 1
    assert seen["warnings"] == []


def test_red_message_lists_both_sets(vault_pair, full_run):
    """C4: вердикт по связанным, свидетельство — оба перечня раздельно."""
    vault_pair.touch(LOG)
    vault_pair.touch(CONTRACT)
    _code, seen, _out = full_run(vault_pair.config())
    (red,) = channel_two(seen["reds"], "vault_uncommitted")
    assert f"связанные с правилами реестра: {CONTRACT}" in red
    assert f"прочие пути канона: {LOG}" in red


def test_mutation_d_ahead_unbound(vault_pair, full_run):
    """(г) HEAD вольта впереди клона, правка трогает только несвязанный файл."""
    vault_pair.touch(LOG)
    vault_pair.commit("несвязанная правка")
    vault_pair.push()
    vault_pair.clone_fetch()
    code, seen, _ = full_run(vault_pair.config())
    assert len(seen["warnings"]) == 1
    assert len(channel_two(seen["warnings"], "vault_ahead_unbound")) == 1
    assert channel_two(seen["reds"], "vault_ahead_of_clone") == []
    # Наблюдение, разошедшееся с предсказанием «0 красных»: канал 1 краснеет сам
    # по себе. Чтобы коммит стал известен клону, он должен быть запушен, а значит
    # клон отстал от своей удалённой ветки. Красный здесь не от сужения канала 2,
    # а от неизменённого канала 1, и детектор под предсказание не правится.
    assert len(seen["reds"]) == 1
    assert len(channel_two(seen["reds"], "clone_behind_vault")) == 1
    assert code == 1


def test_mutation_e_ahead_unpushed_bound(vault_pair, full_run):
    """(д) коммит не запушен: состав клону неизвестен — сужать нечем."""
    vault_pair.touch(CONTRACT)
    vault_pair.commit("связанная правка, не запушена")
    code, seen, _ = full_run(vault_pair.config())
    assert code == 1
    assert len(seen["reds"]) == 1
    (red,) = channel_two(seen["reds"], "vault_ahead_of_clone")
    assert "не запушен" in red and "состав правки не измерен" in red
    assert seen["warnings"] == []


def test_ahead_bound_file_known_commit(vault_pair):
    """Состав известен и трогает связанный файл — красный, оба перечня в тексте."""
    vault_pair.touch(CONTRACT)
    vault_pair.touch(LOG)
    vault_pair.commit("связанная правка")
    vault_pair.push()
    vault_pair.clone_fetch()
    config = vault_pair.config()
    bound = run.registry_source_files(
        yaml.safe_load(open(config["paths"]["registry"], encoding="utf-8")))
    got = run.check_vault_master(config, vault_pair.clone, vault_pair.clone_head(), bound)
    (red,) = infra_lines(got["reds"], "vault_ahead_of_clone")
    assert CONTRACT in red and LOG in red
    assert got["row"]["ahead_files"] == sorted([CONTRACT, LOG])


def test_without_registry_any_canon_path_is_red(vault_pair):
    """Набор связанных файлов не выведен — поведение до сужения."""
    vault_pair.touch(LOG)
    got = run.check_vault_master(vault_pair.config(), vault_pair.clone,
                                 vault_pair.clone_head(), set())
    (red,) = infra_lines(got["reds"], "vault_uncommitted")
    assert "сужать нечем" in red and LOG in red


# ─────────────────────── регрессия канала 1 (C3) ─────────────────────────

def test_clone_behind_still_red_for_unbound(vault_pair, full_run):
    """Канал 1 сужением не тронут: отставание клона красит прогон и тогда,
    когда отставший коммит трогает только несвязанный `00-system/log.md`."""
    vault_pair.touch(LOG)
    vault_pair.commit("несвязанная правка")
    vault_pair.push()
    code, seen, _ = full_run(vault_pair.config())
    assert code == 1
    (red,) = channel_two(seen["reds"], "clone_behind_vault")
    assert "отстаёт" in red


def test_clone_remote_signature_takes_only_config(vault_pair):
    """`check_clone_remote` сужения не получает: параметра у него нет."""
    import inspect
    assert list(inspect.signature(run.check_clone_remote).parameters) == ["config"]


def test_clone_pull_clears_channel_one(vault_pair, full_run):
    vault_pair.touch(LOG)
    vault_pair.commit("несвязанная правка")
    vault_pair.push()
    vault_pair.clone_pull()
    code, seen, _ = full_run(vault_pair.config())
    assert (code, seen["reds"], seen["warnings"]) == (0, [], [])


# ────────────────────────── форма отчёта ─────────────────────────────────

def test_report_row_keeps_dirty_canon_key(vault_pair):
    vault_pair.touch(LOG)
    got = run.check_vault_master(vault_pair.config(), vault_pair.clone,
                                 vault_pair.clone_head(), {CONTRACT, PROMPT_KIT})
    row = got["row"]
    assert row["dirty_canon"] == [LOG]
    assert row["dirty_bound"] == [] and row["dirty_unbound"] == [LOG]
    assert row["bound_files"] == sorted([CONTRACT, PROMPT_KIT])
    assert row["status"] == "vault_uncommitted_unbound"


@pytest.mark.parametrize("status", ["vault_master_unreachable"])
def test_missing_master_is_warning_not_red(vault_pair, status):
    config = vault_pair.config()
    config["vault"]["master_path"] = os.path.join(vault_pair.base, "нет-такого")
    got = run.check_vault_master(config, vault_pair.clone, vault_pair.clone_head(),
                                 {CONTRACT})
    assert got["reds"] == [] and infra_lines(got["warnings"], status)
